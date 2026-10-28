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


# A page's parent, for the Back bar when this tab has no trail to follow - a
# link opened in a new tab, a bookmark, the QR code. Values: (url name, the
# URL kwargs it takes from this page's own kwargs).
BACK_PARENTS = {
    'product_detail': ('product_list', ()),
    'edit_product': ('product_detail', ('pk',)),
    'add_product': ('product_list', ()),
    'product_sales_history': ('product_list', ()),
    'customer_detail': ('customer_search', ()),
    'edit_customer': ('customer_detail', ('customer_id',)),
    'supplier_detail': ('supplier_list', ()),
    'supplier_create': ('supplier_list', ()),
    'supplier_edit': ('supplier_detail', ('supplier_id',)),
    'employee_create': ('employee_list', ()),
    'employee_edit': ('employee_list', ()),
    'store_create': ('store_list', ()),
    'store_edit': ('store_list', ()),
    'sale_order_detail': ('sales_records', ()),
    'sale_order_correction_create': ('sale_order_correction_center', ()),
    'sale_order_correction_edit': ('sale_order_correction_center', ()),
    'ar_new': ('ar_list', ()),
    'ar_detail': ('ar_list', ()),
    'ar_add_payment': ('ar_detail', ('invoice_id',)),
    'ar_add_items': ('ar_detail', ('invoice_id',)),
    'inbound_checklist': ('inbound_checklists', ()),
}


def nav_back(request):
    """``nav_back_fallback``: the parent page's URL, or '' when it has none."""
    from django.urls import NoReverseMatch, reverse
    from .permissions import has_manager_access

    user = getattr(request, 'user', None)
    match = getattr(request, 'resolver_match', None)
    if not user or not user.is_authenticated or not match:
        return {}
    parent = BACK_PARENTS.get(match.url_name)
    if not parent:
        return {'nav_back_fallback': ''}
    name, kwarg_names = parent
    if name == 'inbound_checklists' and has_manager_access(user):
        name = 'inbound'
    try:
        url = reverse(name, kwargs={k: match.kwargs[k] for k in kwarg_names})
    except (KeyError, NoReverseMatch):
        url = ''
    return {'nav_back_fallback': url}
