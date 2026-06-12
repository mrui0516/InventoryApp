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
def sum_list(values):
    """对可迭代对象求和"""
    try:
        return sum(v for v in values if v is not None)
    except Exception:
        return 0


@register.filter
def map_attr(values, attr: str):
    """
    提取对象列表里的某个属性
    用法: queryset|map_attr:"field"
    """
    result = []
    for v in values:
        try:
            val = getattr(v, attr)
            if callable(val):
                val = val()
            result.append(val)
        except Exception:
            continue
    return result
