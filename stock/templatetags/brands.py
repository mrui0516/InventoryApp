"""``{% brand_logo brand size=28 fallback=product.brand %}``: the logo, or the
first letter of the name. ``brand`` may be None (an archived product has lost
its link, a text-only one never had it)."""
from django import template

register = template.Library()


@register.inclusion_tag('stock/_brand_logo.html')
def brand_logo(brand, size=28, fallback=''):
    name = (brand.name if brand else fallback) or ''
    return {
        'url': brand.logo.url if brand and brand.logo else '',
        'name': name,
        'letter': name[:1].upper() or '?',
        'size': size,
    }
