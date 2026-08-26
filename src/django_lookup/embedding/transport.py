# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""HTTP plumbing shared by every hosted provider: SSRF guard, bearer key, backoff, decoding.

The embedding box is shared hardware (notes §Remote GPU): 429 and 503 mean "wait", everything else
means "this batch is lost" — and a lost batch degrades the caller, it never fails the request.
"""

import base64
import time
from typing import Any

import requests

from django_lookup.embedding.base import EmbeddingError
from django_lookup.security.url_guard import assert_safe_url
from django_lookup.settings import embed_allowed_hosts

MAX_BATCH = 64  # notes §Remote GPU: batches of 32-64 keep the card busy without starving other users
_RETRY_STATUS = frozenset({429, 503})
_MAX_TRIES = 3
_BACKOFF_S = 0.5


def data_url(image: bytes, media_type: str = "image/jpeg") -> str:
    """OpenAI-compatible image input: base64 inline, so no third party ever fetches our URLs."""
    return f"data:{media_type};base64,{base64.b64encode(image).decode()}"


def batches(images: list[bytes], size: int = MAX_BATCH) -> list[list[bytes]]:
    return [images[start : start + size] for start in range(0, len(images), size)]


def post_json(url: str, payload: dict, api_key: str, timeout: float) -> dict[str, Any]:
    """POST with backoff on a busy backend. Raises `EmbeddingError` for everything a caller can hit."""
    _assert_reachable(url)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    for attempt in range(_MAX_TRIES):
        response = _request(url, payload, headers, timeout)
        if response.status_code not in _RETRY_STATUS:
            return _decoded(response)
        time.sleep(_BACKOFF_S * 2**attempt)
    raise EmbeddingError(f"embedding backend still busy after {_MAX_TRIES} attempts")


def _assert_reachable(url: str) -> None:
    try:
        assert_safe_url(url, allowed_hosts=embed_allowed_hosts())
    except ValueError as exc:
        raise EmbeddingError(f"embedding URL rejected by the SSRF guard: {exc}") from exc


def _request(url: str, payload: dict, headers: dict[str, str], timeout: float) -> requests.Response:
    try:
        return requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise EmbeddingError(f"embedding request failed: {exc}") from exc


def _decoded(response: requests.Response) -> dict[str, Any]:
    if not response.ok:
        raise EmbeddingError(f"embedding backend answered HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise EmbeddingError(f"embedding backend answered with non-JSON: {exc}") from exc
