"""The Orders page for managers: Sales | Purchases | Analysis over one date range.

Employees keep their own single-day page (views._employee_sales_day_view).

Top to bottom the page has five layers and nothing else: the title and store,
the view switch, one query bar, one summary line, the records. A few decisions
are worth knowing before changing it:

* A search returns whole orders. It used to filter the sale lines first and
  rebuild orders from what was left, so an order holding A (EUR 10) and
  B (EUR 20) listed as EUR 10 when searched for A - and printed as EUR 30.
  Now an order matches or it does not, it always carries its full total, and
  the lines that made it match are marked.
* Payments are counted the way the Today page counts them - from the
  order-level SaleOrderPayment split, so a line paid card + cash shows as
  both - so Cash / Card / MBWay here reconcile against Today.
* Sales follow the active store. Purchases do not: stock is shared between
  the shops, and the Purchases view says so rather than letting a shop's
  purchase total be read as its own spending.
* List or calendar is an explicit choice. It used to be decided by whether
  the URL carried dates, so "This month" and the default page landed in two
  different layouts.
* Profit here is revenue minus the FIFO cost of the goods sold - gross
  profit. Part of the page used to call the same number "Net Profit".
"""
from calendar import Calendar, monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone

from .models import InboundOrder, Purchase, Sale, SaleOrder
from .permissions import (has_admin_access, has_manager_access,
                          has_order_reconciliation_access,
                          has_sales_sensitive_access)
from .services.dashboard import (build_yearly_sales_overview,
                                 order_tender_amounts, resolve_year)
from .services.profit import sale_profit_map_for_sale_ids
from .stores import resolve_active_store, scope_sales_by_store
from .views import build_product_label, get_product_image_url

PAGE_SIZE = 40
VIEWS = ('sales', 'purchases', 'analysis')
LAYOUTS = ('list', 'calendar')
DEFAULT_DAYS = 7          # the page opens on the last seven days, today included
PAYMENT_LABELS = dict(Sale.PAYMENT_METHOD_CHOICES)
ZERO = Decimal('0.00')


# ---------------------------------------------------------------- parameters
def _parse_date(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m-%d').date()
    except ValueError:
        return None


def resolve_params(get, today):
    """Read the querystring into one settled set of choices.

    Three older link shapes still arrive from other pages and keep working:
    ``?start_date&end_date`` (a range), ``?month=YYYY-MM`` (that month's
    calendar) and ``?view=year`` (the yearly overview, now under Analysis).
    """
    view = (get.get('view') or '').strip().lower()
    if view == 'year':
        view = 'analysis'
    if view not in VIEWS:
        view = 'sales'

    start = _parse_date(get.get('start_date'))
    end = _parse_date(get.get('end_date'))
    month_raw = (get.get('month') or '').strip()

    layout = (get.get('layout') or '').strip().lower()
    if layout not in LAYOUTS:
        layout = 'calendar' if (month_raw and not start and not end) else 'list'
    if view != 'sales':
        layout = 'list'

    if layout == 'calendar':
        anchor = end or start or today
        year, month = anchor.year, anchor.month
        if month_raw:
            try:
                year, month = (int(part) for part in month_raw.split('-')[:2])
                date(year, month, 1)
            except (ValueError, TypeError):
                year, month = anchor.year, anchor.month
        start = date(year, month, 1)
        end = date(year, month, monthrange(year, month)[1])
    else:
        if not start and not end:
            end = today
            start = today - timedelta(days=DEFAULT_DAYS - 1)
        start = start or end
        end = end or start
        if start > end:
            start, end = end, start

    payment = (get.get('payment') or '').strip().lower()
    if payment not in PAYMENT_LABELS:
        payment = ''
    try:
        page = max(int(get.get('page') or 1), 1)
    except (TypeError, ValueError):
        page = 1

    return {
        'view': view,
        'layout': layout,
        'start': start,
        'end': end,
        # product_q is the old name of the search box; links built with it
        # still search.
        'q': (get.get('q') or get.get('product_q') or '').strip()[:80],
        'payment': payment,
        'page': page,
        'year_raw': get.get('year'),
    }


def page_url(params, **changes):
    """The Orders URL for these choices with some of them changed.

    Values set to None are dropped, so a link carries only what is in force.
    """
    merged = {
        'view': params['view'],
        'layout': params['layout'] if params['view'] == 'sales' else None,
        'start_date': params['start'].isoformat(),
        'end_date': params['end'].isoformat(),
        'q': params['q'] or None,
        'payment': params['payment'] or None,
    }
    if merged['layout'] == 'list':
        merged['layout'] = None
    if merged['view'] == 'sales':
        merged['view'] = None
    merged.update(changes)
    clean = [(key, value) for key, value in merged.items()
             if value not in (None, '')]
    base = reverse('sales_records')
    return f"{base}?{urlencode(clean)}" if clean else base


# ---------------------------------------------------------------- sales
def _pay_display(pay):
    return [
        {'code': method, 'label': PAYMENT_LABELS.get(method, (method or 'Other').title()),
         'amount': amount}
        for method, amount in sorted(pay.items(), key=lambda kv: (-kv[1], kv[0] or ''))
        if method and amount
    ]


def load_orders(start, end, *, active_store, store_is_all, show_profit):
    """Every order in the range, whole, newest first."""
    lines = list(scope_sales_by_store(
        Sale.objects
        .annotate(business_at=Coalesce('order__created_at', 'date'))
        .filter(Q(order__created_at__date__gte=start, order__created_at__date__lte=end)
                | Q(order__isnull=True, date__date__gte=start, date__date__lte=end))
        .select_related('order__customer', 'order__store', 'product', 'customer')
        .prefetch_related('product__images', 'order__payments')
        .order_by('-business_at', '-order_id', '-id'),
        active_store, store_is_all))
    profit_map = (sale_profit_map_for_sale_ids([line.id for line in lines])
                  if show_profit and lines else {})

    orders = {}
    for line in lines:
        order = line.order
        key = str(order.id) if order else f'legacy-{line.id}'
        row = orders.get(key)
        if row is None:
            customer = order.customer if (order and order.customer_id) else line.customer
            created = timezone.localtime(order.created_at if order else (line.business_at or line.date))
            row = orders[key] = {
                'key': key,
                'order': order,
                'order_id': order.id if order else None,
                'detail_url': reverse('sale_order_detail', args=[order.id]) if order else '',
                'correct_url': (reverse('sale_order_correction_edit', args=[order.id])
                                if order else ''),
                'customer': customer,
                'customer_name': customer.name if customer else 'Walk-in',
                'customer_url': reverse('customer_detail', args=[customer.id]) if customer else '',
                'store_name': order.store.name if (order and order.store_id) else '',
                'created_at': created,
                'day': created.date(),
                'items': [],
                'qty': 0,
                'amount': ZERO,
                'profit': ZERO,
                'matched_items': [],
            }
        line.display_name = build_product_label(line.product)
        line.line_total = (line.unit_price or ZERO) * line.quantity
        line.payment_label = PAYMENT_LABELS.get(line.payment_method,
                                                (line.payment_method or 'Other').title())
        line.image_url = get_product_image_url(line.product)
        line.line_profit = (profit_map.get(line.id, {}).get('profit', ZERO)
                            if show_profit else ZERO)
        line.is_match = False
        row['items'].append(line)
        row['qty'] += line.quantity
        row['amount'] += line.line_total
        row['profit'] += line.line_profit

    result = []
    for row in orders.values():
        row['items'].sort(key=lambda item: (item.display_name.lower(), item.id))
        if row['order'] is not None:
            pay = order_tender_amounts(row['order'], row['amount'], row['items'])
        else:
            pay = defaultdict(Decimal)
            for item in row['items']:
                pay[item.payment_method] += item.line_total
        row['pay'] = {method: amount for method, amount in pay.items() if method}
        row['pay_display'] = _pay_display(row['pay'])
        row['pay_summary'] = ' + '.join(p['label'] for p in row['pay_display']) or '-'
        result.append(row)
    result.sort(key=lambda row: (row['created_at'], row['order_id'] or 0), reverse=True)
    return result


def _fold(text):
    return (text or '').casefold()


def search_orders(orders, q):
    """Whole orders matching ``q``: an order number, a customer, or a product.

    The lines that matched a product search are marked, so the list can say
    why an order is there; the order's total is never touched.
    """
    if not q:
        return orders
    needle = _fold(q).lstrip('#').strip()
    if not needle:
        return orders
    as_number = needle.isdigit()
    found = []
    for row in orders:
        hit = as_number and row['order_id'] is not None and str(row['order_id']) == needle
        customer = row['customer']
        if customer and any(needle in _fold(value) for value in
                            (customer.name, customer.nif, customer.phone)):
            hit = True
        for item in row['items']:
            product = item.product
            fields = ((product.name, product.brand, product.model, product.barcode,
                       item.display_name) if product else (item.display_name,))
            if any(needle in _fold(value) for value in fields):
                item.is_match = True
                hit = True
        if hit:
            row['matched_items'] = [item for item in row['items'] if item.is_match]
            found.append(row)
    return found


def filter_payment(orders, payment):
    if not payment:
        return orders
    return [row for row in orders if row['pay'].get(payment, ZERO) > 0]


def summarize(orders):
    amount = sum((row['amount'] for row in orders), ZERO)
    count = len(orders)
    pay = defaultdict(Decimal)
    for row in orders:
        for method, value in row['pay'].items():
            pay[method] += value
    return {
        'amount': amount,
        'orders': count,
        'qty': sum(row['qty'] for row in orders),
        'avg': (amount / count) if count else ZERO,
        'profit': sum((row['profit'] for row in orders), ZERO),
        'pay_display': _pay_display(pay),
        'pay': dict(pay),
    }


def day_totals(orders):
    totals = defaultdict(lambda: {'orders': 0, 'qty': 0, 'amount': ZERO})
    for row in orders:
        bucket = totals[row['day']]
        bucket['orders'] += 1
        bucket['qty'] += row['qty']
        bucket['amount'] += row['amount']
    return totals


def paginate(orders, page):
    """One page of rows, each knowing whether it opens a new day.

    The day heading is decided against the row before it - including the last
    row of the previous page - so "load more" never repeats a heading.
    """
    pages = max((len(orders) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    page = min(page, pages)
    first = (page - 1) * PAGE_SIZE
    rows = orders[first:first + PAGE_SIZE]
    previous_day = orders[first - 1]['day'] if first > 0 else None
    for row in rows:
        row['opens_day'] = row['day'] != previous_day
        previous_day = row['day']
    remaining = max(len(orders) - (first + len(rows)), 0)
    return rows, page, remaining


def order_elsewhere(q, start, end, active_store, store_is_all):
    """An order number typed in but outside the date range - say where it is.

    Searching "#1234" with the default last-seven-days would otherwise answer
    "nothing found" for an order that plainly exists.
    """
    needle = (q or '').lstrip('#').strip()
    if not needle.isdigit():
        return None
    order = scope_sales_by_store(SaleOrder.objects.filter(id=int(needle)),
                                 active_store, store_is_all).first()
    if order is None:
        return None
    day = timezone.localtime(order.created_at).date()
    if start <= day <= end:
        return None
    return {'id': order.id, 'day': day}


def month_calendar(orders, year, month, today, params):
    by_day = day_totals(orders)
    weeks = []
    for week in Calendar(firstweekday=0).monthdatescalendar(year, month):
        cells = []
        for cell_day in week:
            bucket = by_day.get(cell_day)
            cells.append({
                'day': cell_day.day,
                'in_month': cell_day.month == month,
                'is_today': cell_day == today,
                'orders': bucket['orders'] if bucket else 0,
                'amount': bucket['amount'] if bucket else ZERO,
                # a day opens as the list for that day
                'url': page_url(params, layout=None, start_date=cell_day.isoformat(),
                                end_date=cell_day.isoformat()),
            })
        weeks.append(cells)
    return weeks


# ---------------------------------------------------------------- purchases
def _annotate_purchase_items(items):
    items = list(items)
    for item in items:
        item.display_name = build_product_label(item.product)
        item.line_total = (item.cost_price or ZERO) * item.quantity
        item.image_url = get_product_image_url(item.product)
        item.is_match = False
    return items


def load_purchases(start, end, q, next_url):
    """Inbound orders and direct purchases in the range, newest first.

    Not filtered by store: stock is shared between the shops.
    """
    groups = []
    inbound = (InboundOrder.objects
               .filter(created_at__date__gte=start, created_at__date__lte=end)
               .select_related('supplier')
               .prefetch_related('items__product', 'items__product__images',
                                 'items__supplier')
               .order_by('-created_at'))
    for order in inbound:
        items = _annotate_purchase_items(order.items.all())
        supplier = order.supplier or next((i.supplier for i in items if i.supplier_id), None)
        created = timezone.localtime(order.created_at)
        groups.append({
            'key': f'in-{order.id}',
            'ref': f'Inbound #{order.id}',
            'short_ref': f'#{order.id}',
            'invoice_no': order.invoice_no or '',
            'supplier_name': supplier.name if supplier else 'No supplier linked',
            'has_supplier': bool(supplier),
            'created_at': created,
            'day': created.date(),
            'items': items,
            'qty': sum(i.quantity for i in items),
            'cost': sum((i.line_total for i in items), ZERO),
            'edit_url': (f"{reverse('inbound_order_edit', args=[order.id])}"
                         f"?{urlencode({'next': next_url})}"),
        })

    direct = (Purchase.objects
              .filter(inbound_order__isnull=True, date__date__gte=start, date__date__lte=end)
              .select_related('product', 'supplier')
              .prefetch_related('product__images')
              .order_by('-date', '-id'))
    by_day = defaultdict(list)
    for purchase in direct:
        by_day[timezone.localtime(purchase.date).date()].append(purchase)
    for day, purchases in by_day.items():
        items = _annotate_purchase_items(purchases)
        for item in items:
            item.edit_url = (f"{reverse('direct_purchase_edit', args=[item.id])}"
                             f"?{urlencode({'next': next_url})}")
        names = sorted({i.supplier.name for i in items if i.supplier_id})
        groups.append({
            'key': f'direct-{day.isoformat()}',
            'ref': 'Direct purchases',
            'short_ref': 'Direct',
            'invoice_no': '',
            'supplier_name': (names[0] if len(names) == 1 else
                              'Mixed suppliers' if names else 'No supplier linked'),
            'has_supplier': bool(names),
            'created_at': timezone.localtime(max(i.date for i in items)),
            'day': day,
            'items': items,
            'qty': sum(i.quantity for i in items),
            'cost': sum((i.line_total for i in items), ZERO),
            'edit_url': '',
        })

    if q:
        needle = _fold(q)
        kept = []
        for group in groups:
            hit = needle in _fold(group['supplier_name']) or needle in _fold(group['invoice_no'])
            for item in group['items']:
                product = item.product
                fields = ((product.name, product.brand, product.model, product.barcode)
                          if product else ()) + (item.display_name,)
                if any(needle in _fold(value) for value in fields):
                    item.is_match = True
                    hit = True
            if hit:
                group['matched_items'] = [i for i in group['items'] if i.is_match]
                kept.append(group)
        groups = kept

    groups.sort(key=lambda group: group['created_at'], reverse=True)
    previous_day = None
    for group in groups:
        group['opens_day'] = group['day'] != previous_day
        previous_day = group['day']
    return groups


# ---------------------------------------------------------------- analysis
def build_analysis(orders, purchases, show_profit):
    sales_by_day = defaultdict(Decimal)
    profit_by_day = defaultdict(Decimal)
    pay = defaultdict(Decimal)
    products = {}
    for row in orders:
        sales_by_day[row['day']] += row['amount']
        profit_by_day[row['day']] += row['profit']
        for method, value in row['pay'].items():
            pay[method] += value
        for item in row['items']:
            stats = products.setdefault(item.product_id, {
                'product': item.product, 'name': item.display_name,
                'qty': 0, 'revenue': ZERO, 'profit': ZERO})
            stats['qty'] += item.quantity
            stats['revenue'] += item.line_total
            stats['profit'] += item.line_profit
    purchases_by_day = defaultdict(Decimal)
    for group in purchases:
        purchases_by_day[group['day']] += group['cost']

    trend = [
        {'label': day.strftime('%m-%d'),
         'sales': float(sales_by_day.get(day, 0)),
         'purchases': float(purchases_by_day.get(day, 0)),
         'profit': float(profit_by_day.get(day, 0)) if show_profit else 0.0}
        for day in sorted(set(sales_by_day) | set(purchases_by_day))
    ]
    payment_chart = [{'label': p['label'], 'amount': float(p['amount'])}
                     for p in _pay_display(pay)]
    ranked = sorted(products.values(), key=lambda s: (s['revenue'], s['qty']), reverse=True)
    top_revenue = ranked[0]['revenue'] if ranked else ZERO
    top_products = [
        {**s, 'bar': round(float(s['revenue']) / float(top_revenue) * 100, 1) if top_revenue else 0}
        for s in ranked[:8]
    ]
    return trend, payment_chart, top_products


# ---------------------------------------------------------------- the page
def orders_page(request):
    today = timezone.localdate()
    params = resolve_params(request.GET, today)
    start, end, view = params['start'], params['end'], params['view']
    show_profit = bool(getattr(request.user, 'is_superuser', False))
    active_store, store_is_all = resolve_active_store(request)

    context = {
        'params': params,
        'view': view,
        'layout': params['layout'],
        'calendar_mode': params['layout'] == 'calendar',
        'start_date': start.isoformat(),
        'end_date': end.isoformat(),
        'q': params['q'],
        'payment': params['payment'],
        'payment_options': list(Sale.PAYMENT_METHOD_CHOICES),
        'show_profit': show_profit,
        'show_sales_sensitive': has_sales_sensitive_access(request.user),
        'show_order_financials': has_order_reconciliation_access(request.user),
        'show_purchases': has_manager_access(request.user),
        'can_correct': has_admin_access(request.user),
        'store_label': 'All stores' if store_is_all or active_store is None else active_store.name,
        'show_store_col': store_is_all or active_store is None,
        'view_urls': {
            'sales': page_url(params, view=None, layout=None),
            'purchases': page_url(params, view='purchases', layout=None, payment=None),
            'analysis': page_url(params, view='analysis', layout=None, q=None, payment=None),
        },
        'layout_urls': {
            'list': page_url(params, layout=None),
            'calendar': page_url(params, layout='calendar', start_date=None, end_date=None,
                                 month=end.strftime('%Y-%m')),
        },
        'quick_urls': {
            'today': page_url(params, layout=None, start_date=today.isoformat(),
                              end_date=today.isoformat()),
            'last7': page_url(params, layout=None,
                              start_date=(today - timedelta(days=DEFAULT_DAYS - 1)).isoformat(),
                              end_date=today.isoformat()),
            'month': page_url(params, layout=None, start_date=today.replace(day=1).isoformat(),
                              end_date=today.isoformat()),
        },
        'quick_active': (
            'today' if (start == end == today) else
            'last7' if (start == today - timedelta(days=DEFAULT_DAYS - 1) and end == today) else
            'month' if (start == today.replace(day=1) and end == today) else ''),
        'payment_label': PAYMENT_LABELS.get(params['payment'], ''),
        'clear_q_url': page_url(params, q=None),
        'clear_payment_url': page_url(params, payment=None),
        'export_url': (f"{reverse('export_sales_purchases_pdf')}?"
                       f"{urlencode({'start_date': start.isoformat(), 'end_date': end.isoformat()})}"),
    }

    if view in ('sales', 'analysis'):
        orders = load_orders(start, end, active_store=active_store,
                             store_is_all=store_is_all, show_profit=show_profit)

    if view == 'sales':
        orders = filter_payment(search_orders(orders, params['q']), params['payment'])
        summary = summarize(orders)
        context.update({
            'summary': summary,
            'total_sales_amount': summary['amount'],
            'total_sales_orders': summary['orders'],
            'total_sales_qty': summary['qty'],
            'total_sales_profit': summary['profit'],
            'elsewhere': order_elsewhere(params['q'], start, end, active_store, store_is_all),
        })
        if params['layout'] == 'calendar':
            context.update({
                'calendar_weeks': month_calendar(orders, start.year, start.month, today, params),
                'calendar_weekdays': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
                'calendar_month_label': start.strftime('%B %Y'),
                'prev_month_url': page_url(params, start_date=None, end_date=None,
                                           month=(start - timedelta(days=1)).strftime('%Y-%m')),
                'next_month_url': page_url(params, start_date=None, end_date=None,
                                           month=(end + timedelta(days=1)).strftime('%Y-%m')),
            })
        else:
            rows, page, remaining = paginate(orders, params['page'])
            totals = day_totals(orders)
            for row in rows:
                row['day_totals'] = totals[row['day']]
            context.update({
                'orders': rows,
                'remaining': remaining,
                'next_batch': min(remaining, PAGE_SIZE),
                'next_page_url': (page_url(params, page=page + 1, fragment='rows')
                                  if remaining else ''),
            })
            if request.GET.get('fragment') == 'rows':
                return render(request, 'stock/_orders_rows.html', context)

    elif view == 'purchases':
        purchases = load_purchases(start, end, params['q'], request.get_full_path())
        context.update({
            'purchases': purchases,
            'total_purchase_amount': sum((g['cost'] for g in purchases), ZERO),
            'total_purchase_orders': len(purchases),
            'total_purchase_qty': sum(g['qty'] for g in purchases),
        })

    else:  # analysis
        purchases = load_purchases(start, end, '', request.get_full_path())
        trend, payment_chart, top_products = build_analysis(orders, purchases, show_profit)
        summary = summarize(orders)
        year = resolve_year(params['year_raw'], today)
        overview = build_yearly_sales_overview(
            year, today, selected_category_ids=[], show_profit=show_profit,
            store=None if store_is_all else active_store)
        year_chart = []
        for row in overview['monthly_rows']:
            month_value = f"{year:04d}-{row['month']:02d}"
            row['detail_url'] = page_url(params, view=None, layout='calendar',
                                         start_date=None, end_date=None, month=month_value)
            year_chart.append({'label': row['label'], 'amount': float(row['amount']),
                               'url': row['detail_url']})
        context.update({
            **overview,
            'show_trend': True,
            'summary': summary,
            'total_sales_amount': summary['amount'],
            'total_sales_profit': summary['profit'],
            'total_purchase_amount': sum((g['cost'] for g in purchases), ZERO),
            'trend_data': trend,
            'payment_chart': payment_chart,
            'top_products': top_products,
            'year_chart': year_chart,
            'year_nav': {
                'prev': page_url(params, year=overview['prev_year']),
                'next': page_url(params, year=overview['next_year']) if overview['next_year'] else '',
                'current': page_url(params, year=today.year),
            },
        })

    return render(request, 'stock/sales_records.html', context)
