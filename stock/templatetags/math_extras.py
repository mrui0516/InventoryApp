from django import template
from decimal import Decimal

register = template.Library()

@register.filter
def mul(a, b):
    """乘法过滤器：a * b"""
    try:
        return (Decimal(str(a)) * Decimal(str(b)))
    except Exception:
        return 0


@register.filter
def digits(value):
    """Keep only digits — used to build wa.me / tel links from a free-form phone."""
    return ''.join(ch for ch in str(value or '') if ch.isdigit())


@register.filter
def absval(value):
    """绝对值过滤器：用于带方向箭头的增减幅展示，避免出现重复负号"""
    try:
        return abs(Decimal(str(value)))
    except Exception:
        return value


@register.filter
def eur_pt(value):
    """``1234.5`` -> ``1.234,50 EUR``, as the Pedido page and its PDF write it."""
    from ..services.doc_pdf import money
    try:
        return money(Decimal(str(value)))
    except Exception:
        return ''
