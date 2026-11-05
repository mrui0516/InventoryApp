"""A brand's logo: any JPG, PNG or WebP, kept as a small PNG.

Opened with Pillow rather than trusted by its name, so a renamed file is
refused. SVG is left out: it can carry scripts.
"""
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image

MAX_BYTES = 2 * 1024 * 1024
MAX_SIDE = 256
FORMATS = {'JPEG', 'PNG', 'WEBP'}
NOT_AN_IMAGE = 'The logo must be a JPG, PNG or WebP image.'


def clean_logo(upload):
    if upload.size > MAX_BYTES:
        raise ValueError('The logo is over 2 MB.')
    try:
        image = Image.open(upload)
        if image.format not in FORMATS:
            raise ValueError(NOT_AN_IMAGE)
        image.load()
    except ValueError:
        raise
    except Exception:
        raise ValueError(NOT_AN_IMAGE)
    image = image.convert('RGBA')
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    out = BytesIO()
    image.save(out, 'PNG', optimize=True)
    return ContentFile(out.getvalue(), name='logo.png')
