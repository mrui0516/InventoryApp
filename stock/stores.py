"""Active-store resolution for the multi-store feature.

Inventory / inbound / suppliers / products are shared across all stores.
Sales, employees, AR and reporting are scoped to the *active* store:

- Employees are locked to their home store (``StoreProfile.store``).
- Managers / admins can switch the active store, or pick "All stores" to see
  an aggregate across every store.
"""
from .models import Store
from .permissions import has_manager_access

ACTIVE_STORE_SESSION_KEY = 'active_store_id'
ALL_STORES = 'all'


def user_home_store(user):
    profile = getattr(user, 'store_profile', None)
    return profile.store if profile else None


def available_stores():
    return list(Store.objects.filter(is_active=True))


def can_switch_store(user):
    return has_manager_access(user)


def resolve_active_store(request):
    """Return ``(store_or_None, is_all)`` for the current request.

    ``store`` is ``None`` together with ``is_all=True`` only for the managers'
    "All stores" aggregate view. Employees always get their home store.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return None, False

    if not can_switch_store(user):
        return user_home_store(user), False

    raw = request.session.get(ACTIVE_STORE_SESSION_KEY)
    if raw == ALL_STORES:
        return None, True
    if raw:
        store = Store.objects.filter(id=raw, is_active=True).first()
        if store:
            return store, False

    # Managers/admins default to the "All stores" aggregate until they pick one.
    return None, True


def store_for_new_sale(request):
    """The concrete store a newly created sale is attributed to (never "all")."""
    store, _is_all = resolve_active_store(request)
    if store:
        return store
    return user_home_store(request.user) or Store.get_default()


def scope_sales_by_store(queryset, store, is_all, *, field='store'):
    """Filter a Sale/SaleOrder/ARInvoice queryset to the active store.

    ``is_all`` (managers' aggregate) returns the queryset unfiltered. A missing
    store also returns it unfiltered (safe default before any store exists).
    """
    if is_all or store is None:
        return queryset
    return queryset.filter(**{field: store})

def scope_products_by_store(queryset, store):
    """Limit a Product queryset to what ``store`` actually sells.

    Stock itself stays shared across stores; this is only about which
    categories a shop puts in front of its staff. A store with no categories
    configured sells everything, so this is a no-op until someone ticks boxes.
    """
    if store is None:
        return queryset
    category_ids = list(store.sellable_categories.values_list('id', flat=True))
    if not category_ids:
        return queryset
    return queryset.filter(category_id__in=category_ids)
