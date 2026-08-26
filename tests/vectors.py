# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Hand-built unit vectors, so a test can aim at an exact cosine instead of hoping for one."""

import math
import random

from django_lookup.constants import EMBEDDING_DIM


def unit_vector(seed: int) -> list[float]:
    generator = random.Random(seed)  # noqa: S311 — reproducible test fixtures, not cryptography
    return _normalised([generator.uniform(-1.0, 1.0) for _ in range(EMBEDDING_DIM)])


def similar_to(vector: list[float], cosine: float, seed: int = 0) -> list[float]:
    """A unit vector whose cosine with `vector` is `cosine` — built from an orthogonal component."""
    other = unit_vector(seed + 10_007)
    projection = sum(a * b for a, b in zip(vector, other, strict=True))
    orthogonal = _normalised([b - projection * a for a, b in zip(vector, other, strict=True)])
    weight = math.sqrt(max(0.0, 1.0 - cosine**2))
    return _normalised([cosine * a + weight * b for a, b in zip(vector, orthogonal, strict=True)])


def _normalised(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]
