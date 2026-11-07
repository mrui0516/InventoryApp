"""B2B -> Overview: every reseller in one row - account, visits, searches, cart,
online orders and sales - so what they do on the catalogue can be read at once.
A handful of grouped queries; the cart value is the only per-customer sum."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Min, Q, Sum
from django.utils import timezone

from ..models import (AccountLink, CartLine, Customer, CustomerAccount, OrderRequest, PortalVisit, RestockRequest,
                      Sale, SaleOrder)

ZERO = Decimal('0.00')
WINDOW_DAYS = 30
STATES = ('active', 'invited', 'suspended', 'none')
STATE_LABELS = {'active': 'Active', 'invited': 'Invited', 'suspended': 'Suspended', 'none': 'No account'}


def _money():
    return ExpressionWrapper(F('unit_price') * F('quantity'), output_field=DecimalField(max_digits=12, decimal_places=2))


def overview(state='', query=''):
    now = timezone.now()
    today = timezone.localdate()
    since = today - timedelta(days=WINDOW_DAYS - 1)
    month_start = today.replace(day=1)

    customers = list(Customer.objects.filter(kind=Customer.REVENDA).order_by('name'))
    ids = [c.pk for c in customers]
    accounts = {a.customer_id: a for a in CustomerAccount.objects.filter(customer_id__in=ids).select_related('user')}
    account_ids = [a.pk for a in accounts.values()]
    user_ids = {a.user_id: a.customer_id for a in accounts.values()}

    links = {}
    for link in (AccountLink.objects.filter(account_id__in=account_ids, used_at__isnull=True)
                 .order_by('account_id', '-created_at')):
        links.setdefault(link.account_id, link)

    visits = {row['account']: row for row in PortalVisit.objects.filter(account_id__in=account_ids, day__gte=since)
              .values('account').annotate(days=Count('id'), views=Sum('views'))}
    searches = defaultdict(list)
    for visit in PortalVisit.objects.filter(account_id__in=account_ids, day__gte=since).order_by('-day'):
        for term in reversed(visit.searches or []):
            if term not in searches[visit.account_id]:
                searches[visit.account_id].append(term)

    requests = {row['customer']: row for row in OrderRequest.objects.filter(customer_id__in=ids).values('customer')
                .annotate(to_confirm=Count('id', filter=Q(status=OrderRequest.SUBMITTED)),
                          confirmed=Count('id', filter=Q(status=OrderRequest.CONFIRMED)),
                          completed=Count('id', filter=Q(status=OrderRequest.COMPLETED)),
                          cancelled=Count('id', filter=Q(status=OrderRequest.CANCELLED)),
                          total=Count('id'))}
    recent = defaultdict(list)
    for order in OrderRequest.objects.filter(customer_id__in=ids).prefetch_related('lines').order_by('-created_at'):
        if len(recent[order.customer_id]) < 5:
            recent[order.customer_id].append(order)

    sales = {row['order__customer']: row for row in Sale.objects.filter(order__customer_id__in=ids)
             .values('order__customer')
             .annotate(total=Sum(_money()), month=Sum(_money(), filter=Q(order__created_at__date__gte=month_start)))}
    last_sale = dict(SaleOrder.objects.filter(customer_id__in=ids).values('customer')
                     .annotate(last=Max('created_at')).values_list('customer', 'last'))
    asked = dict(RestockRequest.objects.filter(Q(customer_id__in=ids) | Q(requested_by_id__in=user_ids),
                                               created_at__gte=now - timedelta(days=90))
                 .values('customer').annotate(n=Count('id')).values_list('customer', 'n'))

    carts = {row['user']: row for row in CartLine.objects.filter(user_id__in=user_ids).values('user')
             .annotate(units=Sum('quantity'), since=Min('added_at'))}
    cart_lines = defaultdict(list)
    for line in CartLine.objects.filter(user_id__in=user_ids).select_related('product'):
        cart_lines[line.user_id].append(line)

    rows = []
    for customer in customers:
        account = accounts.get(customer.pk)
        row_state = account.status if account else 'none'
        link = links.get(account.pk) if account else None
        cart = carts.get(account.user_id, {}) if account else {}
        cart_value = ZERO
        if cart:
            from .wholesale_cart import cart_quote
            from .wholesale_discount import last_discount
            agreed = last_discount(customer.pk)
            customer.portal_discount = agreed['discount'] if agreed else customer.wholesale_discount
            cart_value = cart_quote(account.user, customer, {})['subtotal']
        visit = visits.get(account.pk, {}) if account else {}
        req = requests.get(customer.pk, {})
        sale = sales.get(customer.pk, {})
        rows.append({
            'customer': customer, 'account': account, 'state': row_state, 'state_label': STATE_LABELS[row_state],
            'link': link, 'link_expired': bool(link and link.expires_at <= now),
            'last_seen': account.last_seen_at if account else None,
            'days_active': visit.get('days', 0), 'views': visit.get('views') or 0,
            'searches': searches.get(account.pk, [])[:15] if account else [],
            'to_confirm': req.get('to_confirm', 0), 'confirmed': req.get('confirmed', 0),
            'completed': req.get('completed', 0), 'cancelled': req.get('cancelled', 0), 'requests': req.get('total', 0),
            'recent': recent.get(customer.pk, []), 'last_request': (recent.get(customer.pk) or [None])[0],
            'sales_total': sale.get('total') or ZERO, 'sales_month': sale.get('month') or ZERO,
            'last_sale': last_sale.get(customer.pk),
            'cart_units': cart.get('units') or 0, 'cart_value': cart_value, 'cart_since': cart.get('since'),
            'cart_lines': cart_lines.get(account.user_id, []) if account else [],
            'restock_asks': asked.get(customer.pk, 0),
        })

    counts = {s: sum(1 for r in rows if r['state'] == s) for s in STATES}
    counts['all'] = len(rows)
    data = {
        'counts': counts,
        # The same counts as the menu badge: every request, linked to a customer or not.
        'to_confirm': OrderRequest.objects.filter(status=OrderRequest.SUBMITTED).count(),
        'confirmed': OrderRequest.objects.filter(status=OrderRequest.CONFIRMED).count(),
        'carts': sum(1 for r in rows if r['cart_units']),
        'cart_value': sum((r['cart_value'] for r in rows), ZERO),
        'seen_week': sum(1 for r in rows if r['last_seen'] and r['last_seen'] >= now - timedelta(days=7)),
        'sales_month': sum((r['sales_month'] for r in rows), ZERO),
    }
    if state in STATES:
        rows = [r for r in rows if r['state'] == state]
    if query:
        needle = query.lower()
        rows = [r for r in rows if needle in r['customer'].name.lower() or needle in (r['customer'].phone or '')]
    # Customers with an account first, the most recently active on top; then the rest by last sale.
    order = {'active': 0, 'invited': 1, 'suspended': 2, 'none': 3}
    rows.sort(key=lambda r: (order[r['state']], -(r['last_seen'] or r['last_sale'] or now - timedelta(days=36500)).timestamp(),
                             r['customer'].name.lower()))
    data['rows'] = rows
    return data
