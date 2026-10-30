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


# Where Back goes from each page that belongs to something: up to its parent,
# not to whatever page came before. A record goes back to its list, opened on
# the page that holds it and scrolled to it (``focus`` + the row's
# ``data-focus``); an edit page goes back to the record. If this tab has the
# parent open already in its trail (same list, its filters and page), base.html
# goes back to that instead. Values: (url name, kwargs taken from this page's
# own kwargs, focus kwarg or None, anchor prefix or None).
BACK_PARENTS = {
    'product_detail': ('product_list', (), 'pk', 'p'),
    'edit_product': ('product_detail', ('pk',), None, None),
    'product_sales_history': ('product_list', (), None, None),
    'add_product': ('product_list', (), None, None),
    'customer_detail': ('customer_search', (), 'customer_id', 'c'),
    'edit_customer': ('customer_detail', ('customer_id',), None, None),
    'supplier_detail': ('supplier_list', (), 'supplier_id', 's'),
    'supplier_create': ('supplier_list', (), None, None),
    'supplier_edit': ('supplier_detail', ('supplier_id',), None, None),
    'employee_create': ('employee_list', (), None, None),
    'employee_edit': ('employee_list', (), None, None),
    'store_create': ('store_list', (), None, None),
    'store_edit': ('store_list', (), None, None),
    'sale_order_detail': ('sales_records', (), 'order_id', 'o'),
    'sale_order_correction_create': ('sale_order_correction_center', (), None, None),
    'sale_order_correction_edit': ('sale_order_correction_center', (), None, None),
    'ar_new': ('ar_list', (), None, None),
    'ar_detail': ('ar_list', (), 'invoice_id', 'i'),
    'ar_add_payment': ('ar_detail', ('invoice_id',), None, None),
    'ar_add_items': ('ar_detail', ('invoice_id',), None, None),
    'inbound_checklist': ('inbound_checklists', (), None, None),
}


def nav_back(request):
    """``nav_back_fallback`` (where Back goes), ``nav_back_parent`` (that
    page's path, for the trail) and ``nav_back_anchor`` (the row to land on)."""
    from django.urls import NoReverseMatch, reverse
    from django.utils import timezone
    from .permissions import has_manager_access

    user = getattr(request, 'user', None)
    match = getattr(request, 'resolver_match', None)
    if not user or not user.is_authenticated or not match:
        return {}
    parent = BACK_PARENTS.get(match.url_name)
    if not parent:
        return {'nav_back_fallback': ''}
    name, kwarg_names, focus_kwarg, prefix = parent
    if name == 'inbound_checklists' and has_manager_access(user):
        name = 'inbound'
    try:
        path = reverse(name, kwargs={k: match.kwargs[k] for k in kwarg_names})
    except (KeyError, NoReverseMatch):
        return {'nav_back_fallback': ''}
    url, anchor = path, ''
    if focus_kwarg and focus_kwarg in match.kwargs:
        record = match.kwargs[focus_kwarg]
        anchor = f'{prefix}-{record}'
        query = f'focus={record}'
        if match.url_name == 'sale_order_detail':
            # Orders shows days: open the order's own day
            from .models import SaleOrder
            created = SaleOrder.objects.filter(pk=record).values_list('created_at', flat=True).first()
            if created:
                day = timezone.localtime(created).date().isoformat()
                query = f'start_date={day}&end_date={day}'
        url = f'{path}?{query}#{anchor}'
    return {'nav_back_fallback': url, 'nav_back_parent': path, 'nav_back_anchor': anchor}
