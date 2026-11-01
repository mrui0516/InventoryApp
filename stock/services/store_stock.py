"""Stock per shop: what each store holds, moving units between the shops, and
what the shops owe each other for it (see models/transfers.py).

Moving units splits a batch: the source batch gives up the units (quantity and
remaining both), and a batch with the same product, supplier, delivery, cost and
date takes them in the other shop. Summed over all batches nothing changes - the
same goods arrived on the same day at the same cost - so purchase totals,
supplier figures, the FIFO profit replay and the stock ledger stay as they were.
"""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum

from ..models import (BusinessSettings, Purchase, StockTransfer, StockTransferLine,
                      Store, StoreSettlement)


def active_stores():
    return list(Store.objects.filter(is_active=True).order_by('-is_default', 'id'))


def stock_by_store(product_ids):
    """``{product_id: {store_id: units}}`` for the given products, one query."""
    out = defaultdict(dict)
    rows = (Purchase.objects.filter(product_id__in=list(product_ids), remaining__gt=0)
            .values('product_id', 'store_id').annotate(units=Sum('remaining')).order_by())
    for row in rows:
        out[row['product_id']][row['store_id']] = row['units']
    return out


def shop_split(product_ids):
    """``{product_id: [{'label', 'units'}]}`` for lists - each shop's count,
    by the first word of its name; empty while there is only one shop."""
    shops = active_stores()
    if len(shops) < 2:
        return {}
    counts = stock_by_store(product_ids)
    return {pid: [{'label': s.name.split()[0], 'units': counts.get(pid, {}).get(s.id, 0)} for s in shops]
            for pid in product_ids}


def store_units(product, store):
    qs = Purchase.objects.filter(product=product, remaining__gt=0)
    qs = qs.filter(store=store) if store is not None else qs.filter(store__isnull=True)
    return qs.aggregate(s=Sum('remaining'))['s'] or 0


def sample_reserve(store, units):
    """The opened sample: one of a shop's bottles while it has any."""
    return 1 if store is not None and store.keeps_samples and units >= 1 else 0


def full_price_units(store, units):
    return units - sample_reserve(store, units)


def till_stock(product, store):
    """What the till shows and allows for ``product`` in ``store``.

    ``stock``: full-price units it can sell - its own beyond the sample, plus
    what the other shops can spare. ``sample_here``: it has its opened sample
    to sell. ``shops``: every shop's count, this one marked."""
    counts = stock_by_store([product.id]).get(product.id, {})
    rows, full = [], counts.get(None, 0)
    for shop in active_stores():
        units = counts.get(shop.id, 0)
        full += full_price_units(shop, units)
        rows.append({'name': shop.name, 'units': units, 'here': store is not None and shop.pk == store.pk,
                     'sample': bool(sample_reserve(shop, units))})
    if store is None:
        return {'stock': sum(counts.values()), 'sample_here': False, 'shops': rows}
    return {'stock': full, 'sample_here': bool(sample_reserve(store, counts.get(store.id, 0))), 'shops': rows}


def transfer_markup():
    return BusinessSettings.get().transfer_markup


def _conflict(product):
    from .stock_ops import StockConflictError
    return StockConflictError(f"Stock for {product.display_name} changed during this operation. Please retry.")


def _split(batch, used, to_store, line):
    """Move ``used`` units of ``batch`` into ``to_store``. Returns their cost."""
    product = batch.product
    if used == batch.quantity == batch.remaining:
        # the whole untouched batch changes shop
        updated = Purchase.objects.filter(pk=batch.pk, remaining=used, quantity=used).update(
            store=to_store, transfer_line=line)
        if not updated:
            raise _conflict(product)
        return Decimal(used) * (batch.cost_price or Decimal('0.00'))
    updated = Purchase.objects.filter(pk=batch.pk, remaining__gte=used, quantity__gt=used).update(
        remaining=F('remaining') - used, quantity=F('quantity') - used)
    if not updated:
        raise _conflict(product)
    moved = Purchase.objects.create(
        product=product, supplier_id=batch.supplier_id, inbound_order_id=batch.inbound_order_id,
        store=to_store, quantity=used, remaining=used, cost_price=batch.cost_price, transfer_line=line)
    # date is auto_now_add: the moved units keep the day they were bought
    Purchase.objects.filter(pk=moved.pk).update(date=batch.date)
    return Decimal(used) * (batch.cost_price or Decimal('0.00'))


@transaction.atomic
def move_units(product, quantity, from_store, to_store, *, transfer=None, reason=StockTransfer.MANUAL,
               user=None, sale_order=None, note='', markup=None):
    """Move ``quantity`` units of ``product`` from one shop to the other, oldest
    batches first, and write what the receiving shop owes. Returns the line.

    ``from_store`` may be None for batches that were never given a shop; they
    move without a debt.
    """
    if quantity <= 0:
        raise ValidationError('Quantity must be at least 1.')
    if from_store is not None and to_store is not None and from_store.pk == to_store.pk:
        raise ValidationError('Pick two different shops.')
    batches = Purchase.objects.filter(product=product, remaining__gt=0).select_related('product')
    batches = batches.filter(store=from_store) if from_store is not None else batches.filter(store__isnull=True)
    batches = list(batches.order_by('date', 'id'))
    available = sum(b.remaining for b in batches)
    if available < quantity:
        where = from_store.name if from_store is not None else 'unassigned stock'
        raise ValidationError(f'{where} has only {available} of {product.display_name}.')

    line = None
    if from_store is not None:
        if transfer is None:
            transfer = StockTransfer.objects.create(
                from_store=from_store, to_store=to_store, reason=reason, sale_order=sale_order,
                created_by=user, note=note[:200])
        rate = Decimal('0.00') if transfer.reason == StockTransfer.OPENING else (
            transfer_markup() if markup is None else markup)
        line = StockTransferLine.objects.create(
            transfer=transfer, product=product, quantity=quantity, cost_total=Decimal('0.00'),
            markup_per_unit=rate, amount=Decimal('0.00'))

    left, cost = quantity, Decimal('0.00')
    for batch in batches:
        if left <= 0:
            break
        used = min(left, batch.remaining)
        cost += _split(batch, used, to_store, line)
        left -= used

    if line is not None:
        line.cost_total = cost.quantize(Decimal('0.01'))
        line.amount = Decimal('0.00') if transfer.reason == StockTransfer.OPENING else (
            line.cost_total + line.markup_per_unit * quantity)
        line.save(update_fields=['cost_total', 'amount'])
    return line


def bring_in_for_sale(product, quantity, store, *, user=None, sale_order=None):
    """Make sure ``store`` holds ``quantity`` full-price units of ``product``,
    taking the shortfall from the other shops (their samples stay). Returns
    the units brought in."""
    own = store_units(product, store)
    short = quantity - full_price_units(store, own)
    if short <= 0:
        return 0
    brought = 0
    sources = [s for s in active_stores() if s.pk != store.pk] + [None]   # None: never given a shop
    for source in sources:
        if short <= 0:
            break
        spare = full_price_units(source, store_units(product, source))
        take = min(short, spare)
        if take <= 0:
            continue
        transfer = None
        if source is not None and sale_order is not None:
            transfer = (StockTransfer.objects
                        .filter(sale_order=sale_order, from_store=source, to_store=store,
                                reason=StockTransfer.SALE).first())
        move_units(product, take, source, store, transfer=transfer, reason=StockTransfer.SALE,
                   user=user, sale_order=sale_order)
        short -= take
        brought += take
    if short > 0:
        total = own + sum(store_units(product, s) for s in sources)
        if total >= quantity:
            raise ValidationError(f'Only the shop samples are left of {product.display_name}: '
                                  f'sell it as a sample.')
        raise ValidationError(f'Not enough stock for {product.display_name}. '
                              f'Needed {quantity}, both shops have {total}.')
    return brought


# ------------------------------------------------------- receiving a delivery
def clean_allocation(raw, stores):
    """``{store_id: units}`` from what the page sent (ids may be strings);
    unknown shops and blanks are dropped, units must be whole and >= 0."""
    ids = {s.id for s in stores}
    out = {}
    for key, value in (raw or {}).items():
        if value in (None, ''):
            continue
        try:
            store_id, units = int(key), int(value)
        except (TypeError, ValueError):
            raise ValueError('Shop quantities must be whole numbers.')
        if units < 0:
            raise ValueError('Shop quantities cannot be negative.')
        if store_id in ids and units:
            out[store_id] = units
    return out


def split_for_receipt(raw, quantity, stores, label=''):
    """How ``quantity`` units are split between the shops. With one shop it
    all goes there; with more, the split must add up to the quantity."""
    if len(stores) <= 1:
        return {stores[0].id: quantity} if stores else {}
    alloc = clean_allocation(raw, stores)
    if sum(alloc.values()) != quantity:
        raise ValueError(f'Split {label or "every line"} between the shops: '
                         f'{sum(alloc.values())} of {quantity} placed.')
    return alloc


def receive_batches(*, product, quantity, cost_price, allocation, supplier=None, inbound_order=None):
    """One batch per shop for a received line."""
    stores = {s.id: s for s in Store.objects.filter(id__in=list(allocation))}
    made = []
    for store_id, units in allocation.items():
        made.append(Purchase.objects.create(
            product=product, supplier=supplier, inbound_order=inbound_order, store=stores.get(store_id),
            quantity=units, remaining=units, cost_price=cost_price))
    if not allocation:          # no shops set up at all
        made.append(Purchase.objects.create(
            product=product, supplier=supplier, inbound_order=inbound_order,
            quantity=quantity, remaining=quantity, cost_price=cost_price))
    return made


# --------------------------------------------------------------- the money
def balances():
    """What each pair of shops owes, netted: ``[{debtor, creditor, amount}]``."""
    owed = defaultdict(Decimal)          # (debtor_id, creditor_id) -> amount
    for row in (StockTransferLine.objects.exclude(transfer__reason=StockTransfer.OPENING)
                .values('transfer__from_store', 'transfer__to_store').annotate(total=Sum('amount'))):
        owed[(row['transfer__to_store'], row['transfer__from_store'])] += row['total'] or Decimal('0.00')
    for row in StoreSettlement.objects.values('payer', 'payee').annotate(total=Sum('amount')):
        owed[(row['payer'], row['payee'])] -= row['total'] or Decimal('0.00')
    stores = {s.id: s for s in Store.objects.all()}
    out, seen = [], set()
    for (a, b) in list(owed):
        if (a, b) in seen or (b, a) in seen:
            continue
        seen.add((a, b))
        net = owed[(a, b)] - owed.get((b, a), Decimal('0.00'))
        if net == 0:
            continue
        debtor, creditor = (a, b) if net > 0 else (b, a)
        out.append({'debtor': stores.get(debtor), 'creditor': stores.get(creditor), 'amount': abs(net)})
    return out


@transaction.atomic
def settle(payer, payee, amount, *, settled_on, user=None, note=''):
    if amount is None or amount <= 0:
        raise ValidationError('Enter the amount paid.')
    if payer.pk == payee.pk:
        raise ValidationError('Pick two different shops.')
    return StoreSettlement.objects.create(payer=payer, payee=payee, amount=amount,
                                          settled_on=settled_on, created_by=user, note=note[:200])
