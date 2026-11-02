"""Undo a receipt that should not have happened.

Confirming a receipt is one tap, and a mistaken tap puts stock into the shop
that never arrived. Undoing it by hand means finding the purchase batches,
deleting them, remembering to put the pending lines back and resetting the
order - easy to get half right, and half right is worse than not doing it.

**The one rule that matters: nothing may have been sold from the batches.**
Once a unit has left a batch, its cost is written into the sale that took it
(``Sale.cost_basis``), and deleting the batch would leave that sale describing
stock the app no longer believes existed. So a rollback is refused outright
when any batch has been touched, rather than half-applied.

What it reverses, in the order the receipt created it:

* the purchase batches go, taking their stock with them
* the pending lines come back, exactly as they were before confirmation
* the order returns to awaiting receipt, with its receipt time cleared

Deleting the batches fires the usual signals, so Shopify is told the stock is
gone and perfume prices are recomputed from what is left. Clearing
``received_at`` also takes the order back out of the supplier's lead time,
which is right: it never actually arrived.
"""
from django.db import transaction

from ..models import InboundPendingItem, Purchase


def can_roll_back(order):
    """``(allowed, reason)`` - reason is shown to whoever pressed the button."""
    if order is None:
        return False, 'Order not found.'
    if order.status != 'received':
        return False, 'This order has not been received yet.'

    batches = list(order.items.select_related('product').all())
    if not batches:
        return False, 'This order has no stock batches to remove.'

    return _untouched(batches)


def _untouched(batches):
    """``(allowed, reason)``: nothing sold from the batches, nothing moved to
    the other shop (that transfer would still say what it owes)."""
    sold = [b for b in batches if b.remaining != b.quantity]
    if sold:
        names = ', '.join(sorted({b.product.display_name for b in sold})[:3])
        return False, (
            f'Some of this stock has already been sold ({names}). '
            'Rolling back would leave those sales describing stock that no '
            'longer exists - correct the sales first.')
    moved = [b for b in batches if b.transfer_line_id]
    if moved:
        names = ', '.join(sorted({b.product.display_name for b in moved})[:3])
        return False, (f'Some of it was moved to the other shop ({names}). '
                       'Undo that transfer on Transfers first.')
    return True, ''


def roll_back_receipt(order, user=None):
    """Undo the receipt. Returns ``(ok, message)``; changes nothing on refusal."""
    allowed, reason = can_roll_back(order)
    if not allowed:
        return False, reason

    with transaction.atomic():
        # Re-read inside the transaction: between the page rendering and the
        # button being pressed, someone at the till may have sold one.
        batches = list(Purchase.objects.select_related('product')
                       .filter(inbound_order=order))
        allowed, reason = _untouched(batches)
        if not allowed:
            return False, reason

        # One pending line per product and price, with the split between the
        # shops it was received with, so receiving it again needs no retyping.
        lines = {}
        for batch in batches:
            line = lines.setdefault((batch.product_id, batch.cost_price),
                                    {'product': batch.product, 'quantity': 0, 'allocation': {}})
            line['quantity'] += batch.quantity
            shop = batch.received_store_id or batch.store_id
            if shop:
                line['allocation'][str(shop)] = line['allocation'].get(str(shop), 0) + batch.quantity
        restored = 0
        for (_product_id, cost_price), line in lines.items():
            InboundPendingItem.objects.create(
                inbound_order=order,
                product=line['product'],
                quantity=line['quantity'],
                cost_price=cost_price,
                allocation=line['allocation'],
            )
            restored += 1

        # Deleted one at a time rather than in bulk: a queryset delete fires no
        # per-row signal, and those signals are what tell Shopify the stock is
        # gone and recompute the perfume prices.
        for batch in batches:
            batch.delete()

        order.status = 'pending_receipt'
        order.received_at = None
        order.save(update_fields=['status', 'received_at'])

    return True, (f'Order #{order.id} rolled back. {restored} line(s) are '
                  'awaiting receipt again and the stock has been removed.')


# ------------------------------------------------- a stock-in with no supplier
def can_roll_back_direct(batches):
    """A stock-in made without a supplier (before 2026-11-02 that was allowed):
    the same untouched rule as a receipt."""
    if not batches:
        return False, 'That stock-in is gone already.'
    if any(b.inbound_order_id for b in batches):
        return False, 'Those batches belong to an inbound order: roll back the order instead.'
    return _untouched(batches)


def roll_back_direct(batch_ids):
    """Remove a stock-in made without a supplier. Returns ``(ok, message)``;
    changes nothing on refusal."""
    with transaction.atomic():
        batches = list(Purchase.objects.select_related('product').filter(pk__in=list(batch_ids)))
        if len(batches) != len(set(batch_ids)):
            return False, 'Part of that stock-in is gone already. Reload the page.'
        allowed, reason = can_roll_back_direct(batches)
        if not allowed:
            return False, reason
        units = sum(b.quantity for b in batches)
        products = len({b.product_id for b in batches})
        for batch in batches:       # one at a time: the signals tell Shopify and reprice
            batch.delete()
    return True, f'Stock-in rolled back: {units} unit(s) of {products} product(s) removed.'
