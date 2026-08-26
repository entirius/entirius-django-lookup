# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pure image pre-processing: decode, pre-crop, hash, re-encode. No settings, no DB, no network.

The pre-crop is what makes the whole image layer work on catalog photography (research r01 §5):
product shots sit on a white background that dominates the DCT, so two different products hash
almost identically until the background is cut away. Query and catalog images MUST go through the
very same function — a different pre-crop is a different feature space.
"""

import hashlib
from io import BytesIO

import imagehash
from PIL import Image, ImageOps

# Formats the API accepts; anything else is a client error, not a server problem.
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
MAX_SIDE = 1024  # what the hashes see: big enough that pHash stays stable across re-encodes
# What the embedding backend sees. SigLIP-384 resizes to 384 px itself, so every pixel above
# that is decoded, base64'd, shipped and thrown away: a 1024 px catalog photo is ~176 KB
# against ~33 KB here, for a bit-identical vector. It also keeps the request clear of the
# per-string input caps hosted backends apply (Infinity's text route stops at 122880
# characters, which a 1024 px data URL exceeds twice over).
EMBED_SIDE = 384
_JPEG_QUALITY = 90
# Pixels at or above this luminance are background. Scanned catalog shots are rarely pure 255 white.
_WHITE_THRESHOLD = 247
_HASH_BITS = 64
_MASK64 = (1 << _HASH_BITS) - 1
_SIGN_BIT = 1 << (_HASH_BITS - 1)


class InvalidImage(ValueError):
    """These bytes are not a picture this module can use — a client error, never a server fault."""


def load_and_crop(data: bytes) -> Image.Image:
    """Decode, honour the EXIF orientation, crop the background away, downscale. Raises `InvalidImage`."""
    image = _decoded(data)
    return _downscaled(_cropped(image))


def perceptual_hash(image: Image.Image) -> int:
    """pHash as a signed 64-bit integer — what `Fingerprint.phash` stores.

    Only pHash: dHash was stored next to it for a whole release without a single reader — neither the
    blocking legs nor scoring ever looked at it — so it cost a column and a hash per image for nothing.
    """
    return _signed(imagehash.phash(image))


def hamming(left: int, right: int) -> int:
    """Bits that differ between two stored hashes; the masking keeps the signed values honest."""
    return ((left ^ right) & _MASK64).bit_count()


def encode(image: Image.Image) -> bytes:
    """Bytes handed to the embedding provider — the pre-cropped picture, never the original.

    Downscaled to `EMBED_SIDE` on a copy: callers hash the picture they passed in, and
    `Image.thumbnail` resizes in place, so shrinking the argument would silently move the
    caller's feature space.
    """
    buffer = BytesIO()
    for_embedding = image.copy()
    for_embedding.thumbnail((EMBED_SIDE, EMBED_SIDE), Image.Resampling.LANCZOS)
    for_embedding.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return buffer.getvalue()


def digest(data: bytes) -> str:
    """SHA-1 of the source bytes: the "has this picture changed?" key, not a security primitive."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()


def probe_bytes() -> bytes:
    """A tiny valid image for handshakes (`lookup_doctor`); cheap to embed, useless as evidence."""
    return encode(Image.new("RGB", (8, 8), (128, 128, 128)))


def _decoded(data: bytes) -> Image.Image:
    """`verify()` consumes the file object, so the image is opened twice — the documented Pillow dance."""
    try:
        probe = Image.open(BytesIO(data))
        image_format = probe.format
        probe.verify()
    except Exception as exc:  # Pillow raises whatever the codec raises
        raise InvalidImage(f"unreadable image: {exc}") from exc
    if image_format not in ALLOWED_FORMATS:
        raise InvalidImage(f"unsupported image format {image_format!r} (allowed: {sorted(ALLOWED_FORMATS)})")
    return ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")


def _cropped(image: Image.Image) -> Image.Image:
    """Bounding box of everything that is not background; an all-white picture is left alone."""
    mask = image.convert("L").point(lambda value: 0 if value >= _WHITE_THRESHOLD else 255)
    box = mask.getbbox()
    return image.crop(box) if box else image


def _downscaled(image: Image.Image) -> Image.Image:
    if max(image.size) <= MAX_SIDE:
        return image
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return image


def _signed(value: imagehash.ImageHash) -> int:
    """imagehash yields 64 unsigned bits; BigIntegerField is signed — reinterpret, never truncate."""
    raw = int(str(value), 16)
    return raw - (1 << _HASH_BITS) if raw >= _SIGN_BIT else raw
