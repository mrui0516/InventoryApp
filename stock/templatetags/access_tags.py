from django import template

from ..permissions import has_manager_access

register = template.Library()


@register.filter
def is_manager_user(user):
    return has_manager_access(user)
