"""Template context for the active store (store switcher in the sidebar)."""
from .stores import (
    available_stores,
    can_switch_store,
    resolve_active_store,
    user_home_store,
)


def store_context(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {}

    store, is_all = resolve_active_store(request)
    can_switch = can_switch_store(user)
    return {
        'active_store': store,
        'active_store_is_all': is_all,
        'store_can_switch': can_switch,
        'available_stores': available_stores() if can_switch else [],
        'home_store': user_home_store(user),
    }
