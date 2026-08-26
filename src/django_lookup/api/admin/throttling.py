# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Throttle for the lookup endpoints.

A lookup is two Postgres queries plus scoring over up to 100 candidates, and the image layer will
add an embedding call — cheap enough for interactive use, expensive enough that a CMS loop or a
leaked admin token must not run it unbounded. 60/min/user is roughly one query per second.

The service may override it with `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["lookup_check"]`;
`FALLBACK_RATE` is the safety net, because in DRF `rate = None` means "no throttling at all".
The fallback is deliberately NOT a class-level `rate`: `SimpleRateThrottle.__init__` skips
`get_rate()` when `rate` is already set, which would make the service override dead config.
"""

from django.core.exceptions import ImproperlyConfigured
from rest_framework.throttling import UserRateThrottle

SCOPE = "lookup_check"
IMAGE_SCOPE = "lookup_image"


class LookupThrottle(UserRateThrottle):
    scope = SCOPE
    FALLBACK_RATE = "60/min"

    def get_rate(self) -> str:
        try:
            configured = super().get_rate()
        except ImproperlyConfigured:  # scope missing from DEFAULT_THROTTLE_RATES
            return self.FALLBACK_RATE
        if not configured or "/" not in configured:  # a malformed rate must not disable throttling
            return self.FALLBACK_RATE
        return configured


class LookupImageThrottle(LookupThrottle):
    """Tighter bucket for a lookup that carries a picture: it costs a synchronous embedding call.

    Its own scope, so image traffic cannot exhaust the text budget and vice versa.
    """

    scope = IMAGE_SCOPE
    FALLBACK_RATE = "30/min"
