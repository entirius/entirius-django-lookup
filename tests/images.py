# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Synthetic product photography: a coloured shape on a white background, generated per test.

Generated rather than committed — a binary fixture cannot be varied (rotate, resize, recompress),
which is exactly what the hash-stability tests need.
"""

from io import BytesIO

from PIL import Image, ImageDraw

WHITE = (255, 255, 255)
EXIF_ORIENTATION = 0x0112
ROTATE_90_CW = 6  # EXIF orientation: stored turned left, the viewer must turn it back to the right


def product_image(seed: int = 0, size: tuple[int, int] = (600, 600)) -> Image.Image:
    """A distinctive shape well inside a white margin, so the pre-crop has something to cut."""
    image = Image.new("RGB", size, WHITE)
    draw = ImageDraw.Draw(image)
    width, height = size
    colour = ((seed * 97) % 256, (seed * 53) % 256, (seed * 29) % 256)
    draw.ellipse([width * 0.2, height * 0.25, width * 0.7, height * 0.6], fill=colour)
    draw.rectangle([width * 0.3, height * 0.55, width * 0.6, height * 0.75], fill=(20, 20, 20))
    draw.line([width * 0.25, height * 0.3, width * 0.75, height * 0.7], fill=(0, 0, 200), width=1 + seed % 7)
    return image


def encode(image: Image.Image, image_format: str = "PNG", **options) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def rotated_jpeg(image: Image.Image) -> bytes:
    """The same picture stored rotated with the EXIF tag that undoes it — a phone photo."""
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = ROTATE_90_CW
    return encode(image.rotate(90, expand=True), "JPEG", quality=95, exif=exif)
