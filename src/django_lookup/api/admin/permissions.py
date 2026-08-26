# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView


class IsAdminUser(IsAuthenticated):
    """Authenticated + (is_staff OR is_superuser).

    Same divergence from `rest_framework.permissions.IsAdminUser` (is_staff only) as every other
    Volkanos module: admin endpoints accept both flags. Storefront customers are authenticated —
    `IsAuthenticated` alone would let them read the catalogs through the lookup.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        if not super().has_permission(request, view):
            return False
        return bool(request.user and (request.user.is_staff or request.user.is_superuser))
