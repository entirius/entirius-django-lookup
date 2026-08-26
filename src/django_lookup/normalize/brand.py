# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Brand normalisation: fold, drop legal forms, resolve aliases ('Hewlett-Packard GmbH' -> 'hp')."""

import re

from django_lookup.dictionaries import brand_aliases, legal_forms
from django_lookup.normalize.text import fold


def _strip_legal_forms(folded: str) -> str:
    for form in legal_forms():
        folded = re.sub(rf"(?<![\w]){re.escape(form)}(?![\w])", " ", folded)
    return " ".join(folded.split())


def normalize_brand(raw: str | None) -> str:
    if not raw:
        return ""
    folded = _strip_legal_forms(fold(raw))
    return brand_aliases().get(folded, folded)
