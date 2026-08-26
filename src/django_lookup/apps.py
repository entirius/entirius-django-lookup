# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import logging

from django.apps import AppConfig

logger = logging.getLogger("process")


class LookupConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_lookup"
    verbose_name = "Lookup"
    is_volkanos = True

    def ready(self) -> None:
        from django_lookup import signals
        from django_lookup.embedding.factory import dimension_mismatch

        signals.connect()
        # A settings/column mismatch is a warning here and an error in `manage.py lookup_doctor`:
        # a service must still boot with a broken image layer, it just must not stay quiet about it.
        if message := dimension_mismatch():
            logger.warning("lookup: %s", message)
