"""Shared FIFO stock-mutation helpers.

These use conditional ``UPDATE ... WHERE ...`` statements (via ``QuerySet.update()``
with ``F()`` expressions) rather than read-modify-write plus ``select_for_update()``.
``select_for_update()`` is a silent no-op on SQLite (this project's database —
``has_select_for_update = False`` in Django's SQLite backend), so it would not
actually prevent a lost-update race between two concurrent requests. The
conditional-update pattern below is safe on SQLite and remains correct if the
project is ever migrated to a database that does support row locking.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from ..models import Purchase
from .shopify_push import queue_inventory_push


class StockConflictError(ValidationError):
    """Raised when another request changed stock mid-operation.

    Callers should surface this to the user as "please retry" — the whole
    operation is rolled back (this module's functions run inside their own
    atomic block), so nothing is left half-applied.
    """


@transaction.atomic
def consume_stock_fifo(product, quantity, store=None, *, sample=False, own_only=False, user=None,
                       sale_order=None):
    """Decrement ``Purchase.remaining`` for ``product`` by ``quantity``, oldest batch first.

    With ``store`` the units come out of that shop. A full-price sale leaves the
    shop's opened sample alone and takes what the shop lacks from the other one
    first (a transfer, owed at cost + markup); ``sample=True`` sells the shop's
    own sample and never reaches into the other shop; ``own_only`` (a stock
    correction) takes exactly what the shop holds. Without a store (no shops
    set up) all batches are one pool.

    Returns the actual FIFO **cost** of the consumed units (Σ used × batch cost) so
    the caller can store it on the sale as its true cost basis.

    Raises:
        ValidationError: not enough current stock to cover ``quantity``.
        StockConflictError: a batch's ``remaining`` changed concurrently.
    """
    if quantity <= 0:
        return Decimal('0.00')

    if store is not None:
        from .store_stock import bring_in_for_sale, store_units
        if sample or own_only:
            held = store_units(product, store)
            if held < quantity:
                raise ValidationError(
                    f"{store.name} has no sample of {product.display_name} to sell." if sample else
                    f"{store.name} has only {held} of {product.display_name}.")
        else:
            bring_in_for_sale(product, quantity, store, user=user, sale_order=sale_order)

    batches = Purchase.objects.filter(product=product, remaining__gt=0)
    if store is not None:
        batches = batches.filter(store=store)
    batches = list(batches.order_by('date', 'id'))
    available = sum(p.remaining for p in batches)
    if available < quantity:
        raise ValidationError(
            f"Not enough current stock for {product.display_name}. "
            f"Needed {quantity}, available {available}."
        )

    left = quantity
    cost_basis = Decimal('0.00')
    for batch in batches:
        if left <= 0:
            break
        used = min(left, batch.remaining)
        updated = Purchase.objects.filter(pk=batch.pk, remaining__gte=used).update(
            remaining=F('remaining') - used
        )
        if not updated:
            raise StockConflictError(
                f"Stock for {product.display_name} changed during this operation. Please retry."
            )
        cost_basis += Decimal(used) * (batch.cost_price or Decimal('0.00'))
        left -= used

    if left > 0:
        raise StockConflictError(
            f"Stock for {product.display_name} changed during this operation. Please retry."
        )

    from .pricing import sync_perfume_price
    sync_perfume_price(product)
    # These helpers move stock with QuerySet.update(), which fires no model
    # signal, so a manual "decrease stock" adjustment would never have reached
    # Shopify. Queued here, at the one place the change actually happens.
    queue_inventory_push(product)
    return cost_basis.quantize(Decimal('0.01'))


@transaction.atomic
def restore_stock_fifo(product, quantity, store=None):
    """Increment ``Purchase.remaining`` for ``product`` by ``quantity``, most recent batch first.

    Never pushes a batch's ``remaining`` above its original ``quantity``. With
    ``store`` the units go back into that shop: its own batches first, then
    room in the other shop's batches, moved over (the goods are back where
    they were sold; a transfer already written for them stands).

    Raises:
        ValidationError: not enough total capacity across batches to restore ``quantity``.
        StockConflictError: a batch's ``remaining`` changed concurrently.
    """
    if quantity <= 0:
        return

    batches = list(Purchase.objects.filter(product=product).select_related('product').order_by('-date', '-id'))
    if store is not None:
        # the shop's own batches first; the rest are moved over once refilled
        batches.sort(key=lambda b: b.store_id != store.pk)

    left = quantity
    for batch in batches:
        if left <= 0:
            break
        capacity = batch.quantity - batch.remaining
        if capacity <= 0:
            continue
        restored = min(left, capacity)
        updated = Purchase.objects.filter(
            pk=batch.pk, remaining__lte=batch.quantity - restored
        ).update(remaining=F('remaining') + restored)
        if not updated:
            raise StockConflictError(
                f"Stock for {product.display_name} changed during this operation. Please retry."
            )
        if store is not None and batch.store_id != store.pk:
            from .store_stock import _split
            batch.refresh_from_db()
            _split(batch, restored, store, None)
        left -= restored

    if left > 0:
        raise ValidationError(
            f"Could not restore {quantity} units back into current stock for {product.display_name}. "
            "Please review manual stock adjustments before changing this historical order."
        )

    from .pricing import sync_perfume_price
    sync_perfume_price(product)
    queue_inventory_push(product)
