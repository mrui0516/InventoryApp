"""``{% keep_params "q" %}``: hidden inputs for the page's other filters, so a
search keeps the category, status or sort already chosen (and starts again
from page 1)."""
from django import template
from django.utils.html import format_html_join

register = template.Library()


@register.simple_tag(takes_context=True)
def keep_params(context, *exclude):
    request = context["request"]
    skip = set(exclude) | {"page"}
    pairs = [(k, v) for k, values in request.GET.lists() if k not in skip for v in values if v != ""]
    return format_html_join("", '<input type="hidden" name="{}" value="{}">', pairs)
