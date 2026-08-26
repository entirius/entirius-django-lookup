# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API v2 views — `POST /search/` (ranked hits) and `POST /check/` (hits + verdict).

Two input shapes (cms-search §API shape): a JSON body, or multipart with the very same fields as
form values plus an `image` file. The picture is read into memory, turned into evidence and dropped;
nothing about it is ever persisted.

Thin by design: validate, call the service, serialise. No ORM here, no error formatting either —
unhandled failures reach the v2 exception handler, which logs them and answers with a debug_id
instead of an exception message.
"""

from dataclasses import asdict

from django_utils.api.v2_errors import raise_pydantic_as_drf
from drf_spectacular.utils import extend_schema
from pydantic import ValidationError
from rest_framework import exceptions as drf_exceptions
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_lookup.api.admin.permissions import IsAdminUser
from django_lookup.api.admin.throttling import LookupImageThrottle, LookupThrottle
from django_lookup.constants import MAX_UPLOAD_IMAGE_BYTES
from django_lookup.schemas.requests.lookup import LookupQuery
from django_lookup.schemas.responses.lookup import CheckResponse, SearchResponse
from django_lookup.services import lookup_service
from django_lookup.services.image_prep import InvalidImage

_TAGS = ["Lookup"]
_ERROR_RESPONSES = {400: None, 401: None, 403: None, 429: None}
_MULTIPART = "multipart/form-data"
IMAGE_FIELD = "image"
# Form fields that may repeat; everything else is taken as a single value.
_LIST_FIELDS = frozenset({"scope"})


def _image_bytes(request: Request) -> bytes | None:
    """The uploaded picture, size-capped. The CMS downscales client-side, so 5 MB is generous."""
    uploaded = request.FILES.get(IMAGE_FIELD)
    if uploaded is None:
        return None
    if uploaded.size > MAX_UPLOAD_IMAGE_BYTES:
        raise drf_exceptions.ValidationError(f"{IMAGE_FIELD} exceeds {MAX_UPLOAD_IMAGE_BYTES} bytes")
    return uploaded.read()


def _fields(request: Request) -> dict:
    if _MULTIPART not in (request.content_type or ""):
        if not isinstance(request.data, dict):
            raise drf_exceptions.ValidationError("Request body must be a JSON object.")
        return dict(request.data)
    values = (key for key in request.data if key != IMAGE_FIELD)
    return {key: request.data.getlist(key) if key in _LIST_FIELDS else request.data[key] for key in values}


def _parsed(request: Request) -> tuple[LookupQuery, bytes | None]:
    """`has_image` is always the server's own answer — a client value never reaches the validator."""
    image = _image_bytes(request)
    fields = _fields(request) | {"has_image": image is not None}
    try:
        return LookupQuery(**fields), image
    except ValidationError as exc:
        raise_pydantic_as_drf(exc)


class _LookupView(APIView):
    """Shared wiring — declared explicitly per view, never inherited from service defaults."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    parser_classes = [JSONParser, MultiPartParser]

    def get_throttles(self) -> list:
        """A request carrying a picture pays an embedding call, so it draws on the image bucket."""
        return [LookupImageThrottle()] if self._carries_image() else [LookupThrottle()]

    def _carries_image(self) -> bool:
        """A multipart upload always carries an `image` file; a JSON body may instead carry
        `image_url` — either way the request pays for a guarded outbound fetch plus a synchronous
        embedding call, so both draw on the tighter image bucket.

        `get_throttles()` runs during `initial()`, but AFTER `perform_authentication()` and
        `check_permissions()` (APIView.initial() order) — an unauthenticated or non-admin caller is
        already rejected with 401/403 before this ever touches the body, so parsing it here no
        longer leaks a body-shaped error ahead of auth. A malformed body is not this method's
        problem: fall back to the text bucket and let the view's own parsing raise the real 400."""
        if _MULTIPART in (self.request.content_type or ""):
            return IMAGE_FIELD in self.request.FILES
        try:
            data = self.request.data
        except drf_exceptions.ParseError:
            return False
        return bool(isinstance(data, dict) and data.get("image_url"))


class LookupSearchView(_LookupView):
    @extend_schema(
        tags=_TAGS,
        summary="Search the catalogs for something like this",
        description=(
            "Ranked candidates from the PIM and atlas fingerprints with the evidence behind each "
            "one. No verdict — use /check/ for that. At least one of q, ean, name, mpn, sku, an "
            "image_url or a multipart `image` file. When the embedding backend is unreachable the "
            "answer still arrives, with `image_layer_unavailable` in `warnings`."
        ),
        request=LookupQuery,
        responses={200: SearchResponse, **_ERROR_RESPONSES},
    )
    def post(self, request: Request) -> Response:
        query, image = _parsed(request)
        result = _run(lookup_service.search, query, image)
        payload = SearchResponse(
            query_parsed=asdict(result.parsed),
            hits=[asdict(hit) for hit in result.hits],
            warnings=result.warnings,
        )
        return Response(payload.model_dump(mode="json"))


class LookupCheckView(_LookupView):
    @extend_schema(
        tags=_TAGS,
        summary="Check whether the catalogs already hold this product",
        description=(
            "Search plus pairwise scoring: every candidate gets a score, a decision "
            "(match / review / no_match) and its reasons. Each answer is logged as a DedupDecision."
        ),
        request=LookupQuery,
        responses={200: CheckResponse, **_ERROR_RESPONSES},
    )
    def post(self, request: Request) -> Response:
        query, image = _parsed(request)
        result = _run(lookup_service.check, query, image, user=request.user)
        payload = CheckResponse(
            decision=result.decision,
            query_parsed=asdict(result.parsed),
            candidates=[asdict(hit) for hit in result.candidates],
            warnings=result.warnings,
        )
        return Response(payload.model_dump(mode="json"))


def _run(service, query: LookupQuery, image: bytes | None, **extra):
    """Unusable image bytes are the caller's mistake — 400, not the 500 an unhandled error gives."""
    try:
        return service(query, image_data=image, **extra)
    except InvalidImage as exc:
        raise drf_exceptions.ValidationError(str(exc)) from exc
