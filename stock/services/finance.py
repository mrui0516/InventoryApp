"""The Finance section's figures: what was spent, owed, paid and earned."""
from collections import defaultdict
from decimal import ROUND_DOWN, Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import (BusinessSettings, Expense, ExpenseTemplate, ExpenseTemplateSkip, FinanceLog, InboundOrder,
                      Store, SupplierPayment)
from ..models.expenses import PAID_METHODS

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
