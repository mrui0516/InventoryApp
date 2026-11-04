"""The Finance section's figures: what was spent, owed, paid and earned."""
from collections import defaultdict
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce

from ..models import (ARInvoice, ARPayment, BusinessSettings, Expense, ExpenseTemplate, ExpenseTemplateSkip,
                      FinanceLog, InboundOrder, OwnerFunding, Purchase, Sale, SaleOrder, SaleOrderCharge,
                      SaleOrderPayment, Store, StoreSettlement, SupplierPayment)
from .dashboard import compute_period_headline
from ..models.expenses import PAID_METHODS, SHOP_METHODS

ZERO = Decimal('0.00')


def log(user, action, kind, ref_id, summary):
    FinanceLog.objects.create(user=user, action=action, kind=kind, ref_id=ref_id, summary=summary[:255])


def save_expense(*, store, category, amount, period, paid_on, method, payee, note, user, expense=None, template=None):
    """Add ``expense``, or change the one given. ``period`` lands on the 1st of
    its month; an unpaid expense has no method."""
    if store is None or category is None:
        raise ValidationError('Choose the shop and the category.')
    if amount is None or amount <= 0:
        raise ValidationError('Enter the amount.')
    if period is None:
        raise ValidationError('Choose the month.')
    if paid_on and method not in dict(PAID_METHODS):
        raise ValidationError('Choose how it was paid.')
    row = expense or Expense(created_by=user, template=template)
    before = None if expense is None else f'€{expense.amount}'
    row.store, row.category, row.amount = store, category, amount.quantize(Decimal('0.01'))
    row.period = period.replace(day=1)
    row.paid_on, row.method = paid_on, (method if paid_on else '')
    row.payee, row.note = (payee or '')[:120], (note or '')[:200]
    row.save()
    change = f'{before} -> ' if before else ''
    log(user, 'changed' if expense else 'added', 'expense', row.id,
        f'{row.store.name} · {row.category.name} · {row.period:%Y-%m} · {change}€{row.amount}')
    return row


# ------------------------------------------------------------ monthly costs
def due_templates(period, store=None):
    """The monthly costs of ``period`` not confirmed or skipped yet. Worked out
    when asked, so nothing has to run at the start of a month."""
    templates = ExpenseTemplate.objects.filter(is_active=True).select_related('store', 'category')
    if store is not None:
        templates = templates.filter(store=store)
    done = set(Expense.objects.filter(period=period, template__isnull=False).values_list('template_id', flat=True))
    done |= set(ExpenseTemplateSkip.objects.filter(period=period).values_list('template_id', flat=True))
    return [t for t in templates if t.id not in done]


def confirm_template(template, period, *, amount, paid_on, method, user):
    """The month's expense from ``template``; a second confirm finds the first."""
    found = Expense.objects.filter(template=template, period=period).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return save_expense(store=template.store, category=template.category, amount=amount, period=period,
                                paid_on=paid_on, method=method, payee=template.payee, note=template.note,
                                user=user, template=template)
    except IntegrityError:          # two taps at once: the other one saved it
        return Expense.objects.get(template=template, period=period)


def skip_template(template, period, user):
    ExpenseTemplateSkip.objects.get_or_create(template=template, period=period)
    log(user, 'skipped', 'template', template.id, f'{template} · {period:%Y-%m}')


# --------------------------------------------------------- paying suppliers
CENT = Decimal('0.01')


def _order_lines(order):
    """``[(shop_id, units, cost)]``: what was received, by the shop it was
    delivered to, or what is on its way, by the split saved on each line
    (a line with no split yet gives no shop)."""
    batches = list(order.items.all())
    if batches:
        return [(b.received_store_id, b.quantity, (b.cost_price or ZERO) * b.quantity) for b in batches]
    lines = []
    for item in order.pending_items.all():
        split = {int(k): v for k, v in (item.allocation or {}).items() if str(k).isdigit() and v}
        if not split:
            lines.append((None, item.quantity, (item.cost_price or ZERO) * item.quantity))
        for shop_id, units in split.items():
            lines.append((shop_id, units, (item.cost_price or ZERO) * units))
    return lines


def order_due(order):
    return sum((cost for _shop, _units, cost in _order_lines(order)), ZERO)


def order_paid(order):
    return sum((p.amount for p in order.payments.all()), ZERO)


def payables():
    """``[{supplier, orders: [{order, due, paid, owed}], owed}]``, the most owed
    first. Orders from before ``payables_from`` are left out: nothing says what
    was paid for them. Paying too much shows as a negative."""
    cut_off = BusinessSettings.get().payables_from
    if cut_off is None:
        return []
    orders = (InboundOrder.objects.filter(created_at__date__gte=cut_off)
              .select_related('supplier').prefetch_related('items', 'pending_items', 'payments__store'))
    groups = {}
    for order in orders:
        due, paid = order_due(order), order_paid(order)
        if due == paid:
            continue
        name = order.supplier.name if order.supplier_id else 'No supplier'
        group = groups.setdefault(name, {'supplier': name, 'orders': [], 'owed': ZERO})
        group['orders'].append({'order': order, 'due': due, 'paid': paid, 'owed': due - paid})
        group['owed'] += due - paid
    return sorted(groups.values(), key=lambda g: (-g['owed'], g['supplier']))


def add_payment(order, *, store, amount, paid_on, method, note, user):
    """A payment to the supplier for ``order``. It keeps the share markup in
    force today, so changing the setting later rewrites no debt."""
    if store is None:
        raise ValidationError('Choose the shop paying.')
    if amount is None or amount <= 0:
        raise ValidationError('Enter the amount paid.')
    if paid_on is None:
        raise ValidationError('Choose the day it was paid.')
    if method not in dict(PAID_METHODS):
        raise ValidationError('Choose how it was paid.')
    payment = SupplierPayment.objects.create(
        inbound_order=order, store=store, amount=amount.quantize(CENT), paid_on=paid_on, method=method,
        share_markup=BusinessSettings.get().purchase_share_markup, note=(note or '')[:200], created_by=user)
    log(user, 'added', 'supplier payment', payment.id, f'Inbound #{order.id} · {store.name} · €{payment.amount}')
    return payment


def purchase_shares(payment):
    """``{shop_id: {'cost', 'units'}}``: each shop's part of ``payment``, in
    proportion to the cost it received of that order. The costs add up to the
    payment exactly. Empty until the order has a split and a cost. Worked out
    each time, so receiving, editing or rolling back an order moves it."""
    order = payment.inbound_order
    lines = _order_lines(order)
    total = sum((cost for _shop, _units, cost in lines), ZERO)
    by_shop = defaultdict(lambda: {'cost': ZERO, 'units': 0})
    for shop_id, units, cost in lines:
        by_shop[shop_id]['cost'] += cost
        by_shop[shop_id]['units'] += units
    if total <= 0 or None in by_shop:
        return {}
    shares, left = {}, payment.amount
    shops = sorted(by_shop)
    for shop_id in shops[:-1]:
        cost = (payment.amount * by_shop[shop_id]['cost'] / total).quantize(CENT, rounding=ROUND_DOWN)
        left -= cost
        shares[shop_id] = {'cost': cost, 'units': by_shop[shop_id]['units'] * payment.amount / total}
    shares[shops[-1]] = {'cost': left, 'units': by_shop[shops[-1]]['units'] * payment.amount / total}
    return shares


def _shares(payments=None):
    """``[(payment, shop_id, cost, markup)]`` for the shops other than the one
    that paid: what each owes it."""
    rows = SupplierPayment.objects.select_related('inbound_order').prefetch_related(
        'inbound_order__items', 'inbound_order__pending_items') if payments is None else payments
    out = []
    for payment in rows:
        for shop_id, share in purchase_shares(payment).items():
            if shop_id != payment.store_id:
                markup = (payment.share_markup * share['units']).quantize(CENT)
                out.append((payment, shop_id, share['cost'], markup))
    return out


def share_debts():
    """``[(debtor_id, creditor_id, amount)]``: added to what the shops owe each
    other (``store_stock.balances``)."""
    return [(shop_id, payment.store_id, cost + markup) for payment, shop_id, cost, markup in _shares()]


def share_margins(start, end):
    """The markup on purchase shares, as ``[(store_id, day, amount)]``: like a
    transfer's, plus for the shop that paid and minus for the other."""
    payments = (SupplierPayment.objects.filter(paid_on__gte=start, paid_on__lte=end, share_markup__gt=0)
                .select_related('inbound_order')
                .prefetch_related('inbound_order__items', 'inbound_order__pending_items'))
    out = []
    for payment, shop_id, _cost, markup in _shares(payments):
        if markup:
            out.append((payment.store_id, payment.paid_on, markup))
            out.append((shop_id, payment.paid_on, -markup))
    return out


def recent_payments(limit=30):
    """The last payments, each with what every shop's part of it was."""
    rows = list(SupplierPayment.objects.select_related('store', 'inbound_order__supplier')
                .prefetch_related('inbound_order__items', 'inbound_order__pending_items')[:limit])
    names = {s.id: s.name.split()[0] for s in Store.objects.all()}
    for row in rows:
        row.shares = [{'label': names.get(k, 'No shop'), 'cost': v['cost']}
                      for k, v in purchase_shares(row).items()]
    return rows


# ------------------------------------------------------------ the month
def _in_shop(queryset, store, field='store'):
    return queryset if store is None else queryset.filter(**{field: store})


def _sum(queryset, field='amount'):
    return queryset.aggregate(total=Sum(field))['total'] or ZERO


def previous_month(start):
    return (start - timedelta(days=1)).replace(day=1)


def _spent_by_category(period_start, period_end, store):
    rows = (_in_shop(Expense.objects.filter(period__gte=period_start, period__lte=period_end), store)
            .values('category_id', 'category__name', 'category__sort_order').annotate(total=Sum('amount')))
    return {row['category__name']: (row['total'], row['category__sort_order'], row['category_id']) for row in rows}


def month_profit(start, end, store=None):
    """``{gross, charges, by_category: [{category, amount, previous}], expenses, net}``.

    Gross counts every category and channel, net of card fees and, for one
    shop, with the transfer markup. Shipping and fees taken from customers are
    not sales, so they are added here: the carrier's bill is an expense.
    Expenses count by the month they belong to, paid or not. Supplier payments
    never appear: the goods reach gross profit as cost of sale.
    """
    gross = compute_period_headline(start, end, [], True, store)['profit']
    charges = _sum(_in_shop(SaleOrderCharge.objects.filter(order__created_at__date__gte=start,
                                                           order__created_at__date__lte=end), store, 'order__store'))
    now = _spent_by_category(start, end, store)
    prior_start = previous_month(start)
    before = _spent_by_category(prior_start, start - timedelta(days=1), store)
    names = sorted(set(now) | set(before), key=lambda n: ((now.get(n) or before[n])[1], n))
    rows = [{'category': name, 'category_id': (now.get(name) or before[name])[2],
             'amount': now.get(name, (ZERO,))[0], 'previous': before.get(name, (ZERO,))[0]} for name in names]
    expenses = sum((row['amount'] for row in rows), ZERO)
    return {'gross': gross, 'charges': charges, 'by_category': rows, 'expenses': expenses,
            'net': gross + charges - expenses}


def month_cash(start, end, store=None):
    """The money that moved in the month, by when it moved.

    ``{cash: {in, out, net}, bank: {...}, net, owner_paid, lines: [{label, cash, bank}]}``.
    Cash tenders are Cash; card, MB WAY and IOU payments other than cash are
    Bank. A settlement between the shops is Cash and shows for one shop only:
    over all of them it cancels. What the owner paid personally left neither.
    """
    lines = []

    def line(label, cash=ZERO, bank=ZERO):
        lines.append({'label': label, 'cash': cash, 'bank': bank})

    tenders = (_in_shop(SaleOrderPayment.objects.filter(order__created_at__date__gte=start,
                                                        order__created_at__date__lte=end), store, 'order__store')
               .values('method').annotate(total=Sum('amount')))
    sales = {'cash': ZERO, 'bank': ZERO}
    for row in tenders:
        sales['cash' if row['method'] == 'cash' else 'bank'] += row['total']
    # orders from before payments were recorded, and sales with no order
    legacy = (_in_shop(Sale.objects.filter(order__payments__isnull=True), store)
              .annotate(at=Coalesce('order__created_at', 'date'))
              .filter(at__date__gte=start, at__date__lte=end))
    for sale in legacy.only('unit_price', 'quantity', 'payment_method'):
        sales['cash' if sale.payment_method == 'cash' else 'bank'] += sale.unit_price * sale.quantity
    line('Sales', sales['cash'], sales['bank'])

    collected = {'cash': ZERO, 'bank': ZERO}
    payments = _in_shop(ARPayment.objects.filter(created_at__date__gte=start, created_at__date__lte=end),
                        store, 'invoice__store')
    for row in payments.values('method').annotate(total=Sum('amount')):
        collected['cash' if row['method'] == 'cash' else 'bank'] += row['total']
    if any(collected.values()):
        line('IOU collected', collected['cash'], collected['bank'])

    fees = _sum(_in_shop(SaleOrder.objects.filter(created_at__date__gte=start, created_at__date__lte=end), store),
                'card_fee')
    if fees:
        line('Card fees', ZERO, -fees)

    owner_paid = ZERO

    def spent(label, queryset):
        nonlocal owner_paid
        paid = {row['method']: row['total'] for row in queryset.values('method').annotate(total=Sum('amount'))}
        owner_paid += paid.get('owner', ZERO)
        if paid.get('cash') or paid.get('bank'):
            line(label, -paid.get('cash', ZERO), -paid.get('bank', ZERO))

    funding = _in_shop(OwnerFunding.objects.filter(date__gte=start, date__lte=end), store)
    for kind, label, sign in ((OwnerFunding.IN, 'Owner put in', 1), (OwnerFunding.OUT, 'Owner took out', -1)):
        moved = {row['method']: row['total'] for row in
                 funding.filter(kind=kind).values('method').annotate(total=Sum('amount'))}
        if moved:
            line(label, sign * moved.get('cash', ZERO), sign * moved.get('bank', ZERO))

    spent('Expenses', _in_shop(Expense.objects.filter(paid_on__gte=start, paid_on__lte=end), store))
    spent('Supplier payments', _in_shop(SupplierPayment.objects.filter(paid_on__gte=start, paid_on__lte=end), store))

    if store is not None:
        settled = StoreSettlement.objects.filter(settled_on__gte=start, settled_on__lte=end)
        got, gave = _sum(settled.filter(payee=store)), _sum(settled.filter(payer=store))
        if got or gave:
            line('Between shops', got - gave)

    def totals(key):
        came = sum((row[key] for row in lines if row[key] > 0), ZERO)
        went = -sum((row[key] for row in lines if row[key] < 0), ZERO)
        return {'in': came, 'out': went, 'net': came - went}

    cash, bank = totals('cash'), totals('bank')
    return {'cash': cash, 'bank': bank, 'net': cash['net'] + bank['net'], 'owner_paid': owner_paid, 'lines': lines}


def unpaid(store=None):
    """``{expenses, suppliers, total}``. Suppliers are the company's debt, so
    they count under All stores only."""
    expenses = _sum(_in_shop(Expense.objects.filter(paid_on__isnull=True), store))
    suppliers = sum((g['owed'] for g in payables()), ZERO) if store is None else ZERO
    return {'expenses': expenses, 'suppliers': suppliers, 'total': expenses + suppliers}


def profit_rows(profit, columns):
    """The profit table: ``[{label, total, shops, kind}]``, with a figure for
    each of ``columns`` (``{shop, profit}``) where All stores shows them."""
    def rows_of(key):
        return [c['profit'][key] for c in columns]

    def spent_in(column, name):
        return next((r['amount'] for r in column['profit']['by_category'] if r['category'] == name), ZERO)

    rows = [{'label': 'Gross profit', 'total': profit['gross'], 'shops': rows_of('gross'), 'kind': ''}]
    if profit['charges'] or any(rows_of('charges')):
        rows.append({'label': 'Shipping and fees charged', 'total': profit['charges'],
                     'shops': rows_of('charges'), 'kind': ''})
    for row in profit['by_category']:
        if row['amount']:
            rows.append({'label': row['category'], 'total': -row['amount'],
                         'shops': [-spent_in(c, row['category']) for c in columns], 'kind': ''})
    rows.append({'label': 'Net profit', 'total': profit['net'], 'shops': rows_of('net'), 'kind': 'total'})
    return rows


# ------------------------------------------------------- the owner's money
def save_funding(*, store, kind, amount, date, method, note, user, row=None):
    if store is None:
        raise ValidationError('Choose the shop.')
    if kind not in (OwnerFunding.IN, OwnerFunding.OUT):
        raise ValidationError('Choose put in or taken out.')
    if amount is None or amount <= 0:
        raise ValidationError('Enter the amount.')
    if date is None:
        raise ValidationError('Choose the day.')
    if method not in dict(SHOP_METHODS):
        raise ValidationError('Choose cash or bank.')
    changed = row is not None
    row = row or OwnerFunding(created_by=user)
    row.store, row.kind, row.amount, row.date = store, kind, amount.quantize(CENT), date
    row.method, row.note = method, (note or '')[:200]
    row.save()
    log(user, 'changed' if changed else 'added', 'owner money', row.id,
        f'{row.get_kind_display()} · {store.name} · €{row.amount} · {date}')
    return row


def owner_summary(until, store=None):
    """``{in, out, paid_by_owner, invested, net}`` up to ``until``. What the
    owner paid straight from their pocket counts as money put in."""
    funding = _in_shop(OwnerFunding.objects.filter(date__lte=until), store)
    put_in, taken = _sum(funding.filter(kind=OwnerFunding.IN)), _sum(funding.filter(kind=OwnerFunding.OUT))
    paid = (_sum(_in_shop(Expense.objects.filter(method='owner', paid_on__lte=until), store))
            + _sum(_in_shop(SupplierPayment.objects.filter(method='owner', paid_on__lte=until), store)))
    invested = put_in + paid
    return {'in': put_in, 'out': taken, 'paid_by_owner': paid, 'invested': invested, 'net': invested - taken}


def business_value():
    """What the business holds and owes now, for all shops:
    ``{stock, receivable, owed_suppliers, unpaid_expenses, value}``. Stock is at
    cost. Cash in hand and in the bank are not recorded, so they are left out."""
    line_cost = ExpressionWrapper(F('remaining') * F('cost_price'),
                                  output_field=DecimalField(max_digits=14, decimal_places=2))
    stock = (Purchase.objects.filter(remaining__gt=0, product__archived_at__isnull=True)
             .aggregate(total=Sum(line_cost))['total'] or ZERO)
    receivable = sum((i.total_amount - i.amount_paid for i in ARInvoice.objects.exclude(status='paid')), ZERO)
    owed = unpaid()
    value = stock + receivable - owed['suppliers'] - owed['expenses']
    return {'stock': stock, 'receivable': receivable, 'owed_suppliers': owed['suppliers'],
            'unpaid_expenses': owed['expenses'], 'value': value}


def profit_to_date(until, store=None):
    """Net profit from the month Finance started (``payables_from``), the first
    one expenses exist for, to ``until``: its first month is that month's net."""
    start = BusinessSettings.get().payables_from
    if start is None or until < start.replace(day=1):
        return ZERO
    start = start.replace(day=1)
    gross = compute_period_headline(start, until, [], True, store)['profit']
    charges = _sum(_in_shop(SaleOrderCharge.objects.filter(order__created_at__date__gte=start,
                                                           order__created_at__date__lte=until), store, 'order__store'))
    spent = _sum(_in_shop(Expense.objects.filter(period__gte=start, period__lte=until), store))
    return gross + charges - spent
