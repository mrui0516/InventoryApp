"""``{% static_v 'css/app.css' %}``: a static URL that changes when the file does.

PythonAnywhere serves /static/ with no Cache-Control header, so a browser
decides for itself how long to keep app.css - and kept the old one for hours
after a deploy, while the new HTML already used new class names: pages showed
up unstyled. Adding ``?v=<file's modified time>`` gives every changed file a
new URL, so a browser has to fetch it; unchanged files keep theirs and stay
cached.
"""
import os
from functools import lru_cache

from django import template
from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=None)
def _version(path):
    # Looked up once per process: a deploy reloads the app, which clears it.
    # The collected copy first - that is the file the server hands out, so a
    # git pull without collectstatic does not put old CSS under a new URL.
    collected = os.path.join(settings.STATIC_ROOT, path) if settings.STATIC_ROOT else ''
    found = collected if collected and os.path.exists(collected) else finders.find(path)
    if not found:
        return ''
    try:
        return str(int(os.path.getmtime(found)))
    except OSError:
        return ''


@register.simple_tag
def static_v(path):
    url = static(path)
    version = _version(path)
    return f'{url}?v={version}' if version else url
