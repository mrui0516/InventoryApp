"""Shared FIFO stock-mutation helpers.

These use conditional ``UPDATE ... WHERE ...`` statements (via ``QuerySet.update()``
with ``F()`` expressions) rather than read-modify-write plus ``select_for_update()``.
``select_for_update()`` is a silent no-op on SQLite (this project's database —
``has_select_for_update = False`` in Django's SQLite backend), so it would not
actually prevent a lost-update race between two concurrent requests. The
conditional-update pattern below is safe on SQLite and remains correct if the
project is ever migrated to a database that does support row locking.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from ..models import Purchase


class StockConflictError(ValidationError):
    """Raised when another request changed stock mid-operation.

    Callers should surface this to the user as "please retry" — the whole
    operation is rolled back (this module's functions run inside their own
    atomic block), so nothing is left half-applied.
    """


@transaction.atomic
def consume_stock_fifo(product, quantity):
    """Decrement ``Purchase.remaining`` for ``product`` by ``quantity``, oldest batch first.

    Raises:
        ValidationError: not enough current stock to cover ``quantity``.
        StockConflictError: a batch's ``remaining`` changed concurrently.
    """
    if quantity <= 0:
        return

    batches = list(
        Purchase.objects.filter(product=product, remaining__gt=0).order_by('date', 'id')
    )
    available = sum(p.remaining for p in batches)
    if available < quantity:
        raise ValidationError(
            f"Not enough current stock for {product.display_name}. "
            f"Needed {quantity}, available {available}."
        )

    left = quantity
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
        left -= used

    if left > 0:
        raise StockConflictError(
            f"Stock for {product.display_name} changed during this operation. Please retry."
        )


@transaction.atomic
def restore_stock_fifo(product, quantity):
    """Increment ``Purchase.remaining`` for ``product`` by ``quantity``, most recent batch first.

    Never pushes a batch's ``remaining`` above its original ``quantity``.

    Raises:
        ValidationError: not enough total capacity across batches to restore ``quantity``.
        StockConflictError: a batch's ``remaining`` changed concurrently.
    """
    if quantity <= 0:
        return

    batches = list(Purchase.objects.filter(product=product).order_by('-date', '-id'))

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
        left -= restored

    if left > 0:
        raise ValidationError(
            f"Could not restore {quantity} units back into current stock for {product.display_name}. "
            "Please review manual stock adjustments before changing this historical order."
        )
