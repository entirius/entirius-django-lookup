# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Image pre-processing: the pre-crop, EXIF, hash stability and the format gate.

Hash stability under resize/recompress is the property the whole pHash leg rests on (research
r01 §1); if it breaks, `image_near_exact` stops firing on re-uploaded catalog photos.
"""

import pytest
from PIL import Image

from django_lookup.services.image_prep import (
    MAX_SIDE,
    InvalidImage,
    digest,
    encode,
    hamming,
    load_and_crop,
    perceptual_hash,
    probe_bytes,
)
from tests import images

STABLE_DISTANCE = 5  # research r01 §4: <= 5 is "the same picture"


def _hash(data: bytes) -> int:
    return perceptual_hash(load_and_crop(data))


def test_the_white_background_is_cropped_away():
    original = images.product_image(1)
    cropped = load_and_crop(images.encode(original))
    assert cropped.size < original.size


def test_an_all_white_picture_survives_the_crop():
    blank = load_and_crop(images.encode(Image.new("RGB", (64, 64), images.WHITE)))
    assert blank.size == (64, 64)


def test_a_large_picture_is_downscaled():
    huge = load_and_crop(images.encode(images.product_image(2, size=(3000, 2000))))
    assert max(huge.size) <= MAX_SIDE


def test_exif_orientation_is_undone_before_hashing():
    """A phone photo stores the sensor orientation in EXIF; without the transpose it perceptual_hash as a
    different product and its embedding is a rotated one."""
    original = images.product_image(3)
    assert hamming(_hash(images.rotated_jpeg(original)), _hash(images.encode(original))) <= STABLE_DISTANCE


def test_the_hash_survives_a_resize():
    original = images.product_image(4)
    smaller = original.resize((300, 300))
    assert hamming(_hash(images.encode(original)), _hash(images.encode(smaller))) <= STABLE_DISTANCE


def test_the_hash_survives_a_jpeg_recompression():
    original = images.product_image(5)
    recompressed = images.encode(original, "JPEG", quality=40)
    assert hamming(_hash(images.encode(original)), _hash(recompressed)) <= STABLE_DISTANCE


def test_different_products_hash_far_apart():
    left, right = _hash(images.encode(images.product_image(6))), _hash(images.encode(images.product_image(60)))
    assert hamming(left, right) > STABLE_DISTANCE


def test_the_hash_fits_a_signed_bigint():
    """BigIntegerField is signed; an unsigned 64-bit value would overflow the column."""
    value = _hash(images.encode(images.product_image(7)))
    assert -(2**63) <= value < 2**63


def test_hamming_is_correct_across_the_sign_boundary():
    assert hamming(-1, 0) == 64
    assert hamming(-1, -1) == 0


@pytest.mark.parametrize(
    "data",
    [b"", b"not an image at all", images.encode(images.product_image(8), "GIF")],
    ids=["empty", "garbage", "unsupported-format"],
)
def test_unusable_bytes_raise_invalid_image(data):
    with pytest.raises(InvalidImage):
        load_and_crop(data)


def test_encode_round_trips_and_digest_is_stable():
    image = load_and_crop(images.encode(images.product_image(9)))
    assert digest(encode(image)) == digest(encode(image))
    assert load_and_crop(encode(image)).size == image.size


def test_probe_bytes_are_a_usable_image():
    assert load_and_crop(probe_bytes()).size == (8, 8)
