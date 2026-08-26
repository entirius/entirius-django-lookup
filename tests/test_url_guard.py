# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""SSRF guard: schemes, private ranges, the allowlist and the redirect / size caps.

Fetching an image by URL is a server-side request the caller chooses, which is the classic pivot
into the internal network — the guard must hold on every redirect hop, not just the first URL.
"""

import pytest
from django.test import override_settings

from django_lookup.security import url_guard
from django_lookup.security.url_guard import MAX_REDIRECTS, assert_safe_url, safe_get

PUBLIC = "https://93.184.216.34/image.jpg"
PRIVATE = "http://10.0.0.5/image.jpg"


class _Response:
    def __init__(self, status_code=200, headers=None, chunks=(b"data",)):
        self.status_code, self.headers, self._chunks = status_code, headers or {}, chunks
        self.closed = False

    def iter_content(self, chunk_size):  # noqa: ARG002 — mirrors requests.Response
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


@pytest.fixture
def get(monkeypatch):
    """Answers the guard's requests in order; records the URLs it actually dialled."""
    calls, answers = [], []

    def fake_get(url, timeout, stream, allow_redirects):  # noqa: ARG001 — mirrors requests.get
        calls.append(url)
        return answers.pop(0) if answers else _Response()

    monkeypatch.setattr(url_guard.requests, "get", fake_get)
    return type("Get", (), {"calls": calls, "answers": answers})


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host/x", "gopher://host"])
def test_only_http_schemes_pass(url):
    with pytest.raises(ValueError, match="scheme"):
        assert_safe_url(url)


def test_a_url_without_a_host_is_rejected():
    with pytest.raises(ValueError, match="hostname"):
        assert_safe_url("http:///no-host")


def test_a_private_address_is_blocked_by_default():
    with pytest.raises(ValueError, match="internal host"):
        assert_safe_url(PRIVATE)


def test_the_allowlist_lets_one_named_host_through():
    """This is how the in-network embedding service is reachable without opening the whole range."""
    assert_safe_url("http://10.0.0.5/x", allowed_hosts=["10.0.0.5"])


def test_the_master_switch_disables_the_ip_check():
    with override_settings(LOOKUP_BLOCK_PRIVATE_HOSTS=False):
        assert_safe_url(PRIVATE)


def test_a_public_address_is_fetched(get):
    assert safe_get(PUBLIC, timeout=1, cap=1024).content == b"data"


def test_the_content_type_travels_with_the_body(get):
    get.answers.append(_Response(headers={"Content-Type": "image/png"}))
    assert safe_get(PUBLIC, timeout=1, cap=1024).content_type == "image/png"


def test_a_redirect_into_the_private_range_is_refused(get):
    get.answers.append(_Response(status_code=302, headers={"Location": PRIVATE}))
    with pytest.raises(ValueError, match="internal host"):
        safe_get(PUBLIC, timeout=1, cap=1024)
    assert get.calls == [PUBLIC]  # the private hop was never dialled


def test_a_redirect_to_an_allowlisted_private_host_is_followed(get):
    """The same allowlist that lets the embedding client reach a private host works here too."""
    get.answers.append(_Response(status_code=302, headers={"Location": PRIVATE}))
    assert safe_get(PUBLIC, timeout=1, cap=1024, allowed_hosts=["10.0.0.5"]).content == b"data"
    assert get.calls == [PUBLIC, PRIVATE]


def test_a_redirect_to_a_non_allowlisted_private_host_is_still_refused(get):
    get.answers.append(_Response(status_code=302, headers={"Location": PRIVATE}))
    with pytest.raises(ValueError, match="internal host"):
        safe_get(PUBLIC, timeout=1, cap=1024, allowed_hosts=["some-other-host"])
    assert get.calls == [PUBLIC]


def test_a_redirect_chain_is_bounded(get):
    get.answers.extend(_Response(status_code=302, headers={"Location": PUBLIC}) for _ in range(MAX_REDIRECTS + 1))
    with pytest.raises(ValueError, match="too many redirects"):
        safe_get(PUBLIC, timeout=1, cap=1024)


def test_a_redirect_without_a_location_is_an_error(get):
    get.answers.append(_Response(status_code=302))
    with pytest.raises(ValueError, match="Location"):
        safe_get(PUBLIC, timeout=1, cap=1024)


def test_an_oversized_body_is_aborted_mid_stream(get):
    get.answers.append(_Response(chunks=(b"x" * 10, b"y" * 10)))
    with pytest.raises(ValueError, match="cap"):
        safe_get(PUBLIC, timeout=1, cap=15)
