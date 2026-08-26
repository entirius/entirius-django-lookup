# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""SSRF guard for outbound fetches: the embedding endpoint and remote catalog images.

Deliberately a sibling of `django_atlas.security.url_guard` rather than a shared import — the lookup
module must never depend on a catalog module, so the pattern is repeated here with its own settings.

`LOOKUP_BLOCK_PRIVATE_HOSTS = False` drops the IP check wholesale (zeno dev, where the fixtures and
embed containers are private); `LOOKUP_EMBED_ALLOWED_HOSTS` punches a hole for named hosts only, which
is what a production deployment with the embedding service on the private network uses.
"""

import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from django_lookup.settings import block_private_hosts

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 3
_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
_CHUNK = 65536


@dataclass(frozen=True)
class Fetched:
    content: bytes
    content_type: str


def _is_internal_ip(host: str) -> bool:
    try:
        addr_info = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve host: {host}") from exc
    return any(_internal(sockaddr[0]) for *_rest, sockaddr in addr_info)


def _internal(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def assert_safe_url(url: str, allowed_hosts: Iterable[str] = ()) -> None:
    """Raise `ValueError` when the URL is not safe to fetch server-side."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"disallowed URL scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("URL missing hostname")
    if parsed.hostname in set(allowed_hosts) or not block_private_hosts():
        return
    if _is_internal_ip(parsed.hostname):
        raise ValueError(f"internal host blocked: {parsed.hostname}")


def safe_get(url: str, timeout: float, cap: int, allowed_hosts: Iterable[str] = ()) -> Fetched:
    """GET with the host re-validated on every redirect hop and the body streamed up to `cap` bytes.

    Redirects are never auto-followed: `allow_redirects=True` would connect to each hop before
    `assert_safe_url` ever saw it, which is exactly the SSRF this guard exists to stop. `allowed_hosts`
    is the same allowlist `assert_safe_url` takes directly — one policy for every entry point, so a
    host allowlisted for the embedding client is reachable for a remote catalog image too.
    """
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        assert_safe_url(current_url, allowed_hosts=allowed_hosts)
        response = requests.get(current_url, timeout=timeout, stream=True, allow_redirects=False)
        if response.status_code in _REDIRECT_STATUS_CODES:
            current_url = urljoin(current_url, _location(response))
            continue
        with response:
            response.raise_for_status()
            return Fetched(content=_body(response, cap), content_type=response.headers.get("Content-Type", ""))
    raise ValueError(f"too many redirects (> {MAX_REDIRECTS}) fetching {url}")


def _location(response: requests.Response) -> str:
    location = response.headers.get("Location")
    response.close()
    if not location:
        raise ValueError("redirect response missing Location header")
    return location


def _body(response: requests.Response, cap: int) -> bytes:
    body = bytearray()
    for chunk in response.iter_content(chunk_size=_CHUNK):
        body.extend(chunk)
        if len(body) > cap:
            raise ValueError(f"response body exceeds the cap of {cap} bytes")
    return bytes(body)
