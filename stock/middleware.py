"""Keeps customer accounts inside the wholesale catalogue.

Default deny: a customer may open only the pages named in CUSTOMER_PAGES, so an
internal page added later is closed to customers without anyone remembering to.
A GET elsewhere goes back to the catalogue; anything else is refused.
"""
from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse

from .services import portal

CUSTOMER_PAGES = {
    'catalog', 'wholesale_cart', 'wholesale_cart_add', 'wholesale_cart_update', 'wholesale_cart_summary',
    'wholesale_cart_submit', 'wholesale_orders', 'wholesale_order', 'wholesale_order_cancel', 'wholesale_order_edit',
    'wholesale_order_edit_stop', 'wholesale_sale', 'wholesale_pick_search', 'restock_request',
    'portal_home', 'portal_login', 'portal_logout', 'portal_invite',
}
PUBLIC_PAGES = {'portal_login', 'portal_invite', 'portal_home'}


def _page_name(path):
    try:
        return resolve(path).url_name
    except Resolver404:
        return None


class PortalGuard:
    def __init__(self, get_response):
        self.get_response = get_response
        self.open_prefixes = tuple(p for p in (settings.STATIC_URL, settings.MEDIA_URL) if p)

    def __call__(self, request):
        if request.path.startswith(self.open_prefixes):
            return self.get_response(request)
        name = _page_name(request.path_info)
        if request.user.is_authenticated:
            account = portal.account_of(request)
            if account is not None:
                if not portal.may_enter(account):
                    logout(request)
                    return redirect('portal_login')
                if name not in CUSTOMER_PAGES:
                    if request.method in ('GET', 'HEAD'):
                        return redirect('catalog')
                    return HttpResponseForbidden('Not available.')
                portal.record_visit(account, request, name)
        elif name in CUSTOMER_PAGES and name not in PUBLIC_PAGES:
            return redirect(f"{reverse('portal_login')}?next={request.get_full_path()}")
        return self.get_response(request)
