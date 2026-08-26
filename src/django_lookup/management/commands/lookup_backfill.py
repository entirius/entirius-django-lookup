# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Fill the fingerprint table from every configured provider.

`--images` is the second half: it does not touch the text columns at all, it hands every existing
fingerprint to the `lookup` queue so the worker fetches, hashes and embeds its main picture.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from django_lookup.services import backfill_service


class Command(BaseCommand):
    help = "Build/refresh fingerprints from the configured lookup providers."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--kind", help="Only this kind (default: every kind in LOOKUP_PROVIDERS).")
        parser.add_argument("--since", help="ISO timestamp — only items the provider reports as newer.")
        parser.add_argument("--batch", type=int, default=backfill_service.DEFAULT_BATCH)
        parser.add_argument(
            "--images",
            action="store_true",
            help="Enqueue image hashing/embedding for existing fingerprints instead of rebuilding them.",
        )

    def handle(self, *args, **options) -> None:
        try:
            counts = self._run(options)
        except ValueError as exc:  # unknown kind / unregistered provider
            raise CommandError(str(exc)) from exc
        for kind, count in sorted(counts.items()):
            self.stdout.write(f"{kind}: {count}")

    def _run(self, options: dict) -> dict[str, int]:
        if options["images"]:
            return backfill_service.enqueue_images(options["kind"], backfill_service.IMAGE_TASK_BATCH)
        return backfill_service.backfill(options["kind"], _parse_since(options["since"]), options["batch"])


def _parse_since(raw: str | None):
    if not raw:
        return None
    if (parsed := parse_datetime(raw)) is None:
        raise CommandError(f"--since is not an ISO timestamp: {raw!r}")
    return parsed
