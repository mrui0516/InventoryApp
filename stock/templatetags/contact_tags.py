from django import template

from ..services.contacts import whatsapp_url as _whatsapp_url

register = template.Library()


@register.filter
def whatsapp_url(phone):
    """``912 345 678`` -> ``https://wa.me/351912345678``; '' when unusable."""
    return _whatsapp_url(phone)
