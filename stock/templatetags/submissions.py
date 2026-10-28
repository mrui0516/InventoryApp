import uuid

from django import template
from django.utils.html import format_html

register = template.Library()


@register.simple_tag
def submit_token():
    """A hidden field with a fresh one-time token: the form is recorded once."""
    return format_html('<input type="hidden" name="submit_token" value="{}">', uuid.uuid4().hex)
