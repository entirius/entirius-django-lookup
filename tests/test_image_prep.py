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
    EMBED_SIDE,
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
    assert max(image.size) <= EMBED_SIDE, "this fixture must stay under the embed cap to round-trip"
    assert digest(encode(image)) == digest(encode(image))
    assert load_and_crop(encode(image)).size == image.size


def test_encode_caps_the_embedding_payload_below_the_hashing_size():
    """The backend resizes to EMBED_SIDE itself, and OpenAI-compatible hosts cap the input
    string — a MAX_SIDE JPEG inlined as base64 blows past it and the image leg goes dark."""
    image = load_and_crop(images.encode(images.product_image(2, size=(3000, 2000))))
    assert max(image.size) == MAX_SIDE  # the hashing path keeps the big picture

    assert max(load_and_crop(encode(image)).size) == EMBED_SIDE


def test_encode_leaves_the_callers_image_untouched():
    """`Image.thumbnail` resizes in place. Callers hash the image they passed in, so encode
    shrinking it would move the pHash feature space without a single failing assertion."""
    image = load_and_crop(images.encode(images.product_image(3, size=(3000, 2000))))
    before = image.size
    before_hash = perceptual_hash(image)

    encode(image)

    assert image.size == before
    assert perceptual_hash(image) == before_hash


def test_encode_ships_far_less_than_the_hashing_size():
    """The payload travels as a base64 data URL, so bytes here are bytes on the wire for
    every catalog photo. EMBED_SIDE pixels are all the backend keeps."""
    from django_lookup.embedding import transport

    image = load_and_crop(images.encode(images.product_image(4, size=(3000, 2000))))

    at_embed_side = len(transport.data_url(encode(image)))
    at_max_side = len(transport.data_url(images.encode(image, "JPEG", quality=90)))
    assert at_embed_side * 3 < at_max_side


def test_probe_bytes_are_a_usable_image():
    assert load_and_crop(probe_bytes()).size == (8, 8)
