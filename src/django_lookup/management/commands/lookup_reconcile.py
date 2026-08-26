# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Repair drift between the fingerprint table and the catalogs (lost signals)."""

from django.core.management.base import BaseCommand, CommandError

from django_lookup.services import backfill_service


class Command(BaseCommand):
    help = "Create fingerprints for items that have none, delete rows whose item is gone."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--kind", help="Only this kind (default: every kind in LOOKUP_PROVIDERS).")
        parser.add_argument("--batch", type=int, default=backfill_service.DEFAULT_BATCH)

    def handle(self, *args, **options) -> None:
        try:
            results = backfill_service.reconcile(options["kind"], options["batch"])
        except ValueError as exc:  # unknown kind / unregistered provider
            raise CommandError(str(exc)) from exc
        for kind, result in sorted(results.items()):
            self.stdout.write(f"{kind}: created={result.created} deleted={result.deleted}")
