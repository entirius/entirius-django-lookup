# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.urls import path

from django_lookup.api.admin.views.lookup_views import LookupCheckView, LookupSearchView

urlpatterns = [
    path("search/", LookupSearchView.as_view(), name="admin-lookup-search"),
    path("check/", LookupCheckView.as_view(), name="admin-lookup-check"),
]
