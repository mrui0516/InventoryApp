from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db import transaction

from ..models import Purchase, Sale, SaleOrder, SaleOrderChangeLog


def snapshot_sale_order(order):
    if not order:
        return {}

    items = list(
        order.items
        .select_related('product')
        .order_by('id')
    )

    total_amount = sum((item.unit_price * item.quantity for item in items), 0)
    total_qty = sum((item.quantity for item in items), 0)

    return {
        'order_id': order.id,
        'customer_id': order.customer_id,
        'customer_name': order.customer.name if order.customer else '',
        'note': order.note or '',
        'created_at': order.created_at.isoformat() if order.created_at else '',
        'total_qty': total_qty,
        'total_amount': str(total_amount),
        'items': [
            {
                'sale_id': item.id,
                'product_id': item.product_id,
                'product_name': item.product.display_name,
                'barcode': item.product.barcode,
                'quantity': item.quantity,
                'unit_price': str(item.unit_price),
                'payment_method': item.payment_method,
                'date': item.date.isoformat() if item.date else '',
            }
            for item in items
        ],
    }


def _summarise_quantities(line_items):
    grouped = {}
    for item in line_items:
        product = item['product']
        if not product:
            continue
        if product.id not in grouped:
            grouped[product.id] = {'product': product, 'quantity': 0}
        grouped[product.id]['quantity'] += int(item['quantity'] or 0)
    return list(grouped.values())


def _consume_current_stock(line_items):
    for group in _summarise_quantities(line_items):
        product = group['product']
        quantity_needed = group['quantity']
        if quantity_needed <= 0:
            continue

        available_stock = (
            Purchase.objects
            .filter(product=product, remaining__gt=0)
            .aggregate(total=Sum('remaining'))
            .get('total')
            or 0
        )
        if available_stock < quantity_needed:
            raise ValidationError(
                f"Not enough current stock for {product.display_name}. "
                f"Needed {quantity_needed}, available {available_stock}."
            )

        touched = []
        batches = list(
            Purchase.objects
            .filter(product=product, remaining__gt=0)
            .order_by('date', 'id')
        )
        for batch in batches:
            if quantity_needed <= 0:
                break
            used = min(quantity_needed, batch.remaining)
            batch.remaining -= used
            quantity_needed -= used
            touched.append(batch)

        if touched:
            Purchase.objects.bulk_update(touched, ['remaining'])


def _restore_current_stock(line_items):
    for group in _summarise_quantities(line_items):
        product = group['product']
        quantity_to_restore = group['quantity']
        if quantity_to_restore <= 0:
            continue

        touched = []
        batches = list(
            Purchase.objects
            .filter(product=product)
            .order_by('-date', '-id')
        )
        for batch in batches:
            if quantity_to_restore <= 0:
                break

            capacity = batch.quantity - batch.remaining
            if capacity <= 0:
                continue

            restored = min(quantity_to_restore, capacity)
            batch.remaining += restored
            quantity_to_restore -= restored
            touched.append(batch)

        if quantity_to_restore > 0:
            raise ValidationError(
                f"Could not restore {group['quantity']} units back into current stock for {product.display_name}. "
                "Please review manual stock adjustments before changing this historical order."
            )

        if touched:
            Purchase.objects.bulk_update(touched, ['remaining'])


def log_sale_order_change(*, order, order_id_snapshot, action, changed_by, reason, before_data, after_data):
    return SaleOrderChangeLog.objects.create(
        order=order,
        order_id_snapshot=order_id_snapshot,
        action=action,
        changed_by=changed_by,
        reason=reason or '',
        before_data=before_data or {},
        after_data=after_data or {},
    )


@transaction.atomic
def save_sale_order_correction(*, order, customer, note, order_datetime, line_items, changed_by, reason):
    if not line_items:
        raise ValidationError("Add at least one product line before saving the order.")

    before_data = snapshot_sale_order(order)
    action = 'update' if order else 'create'
    previous_line_items = []

    if action == 'update':
        previous_line_items = [
            {
                'product': item.product,
                'quantity': item.quantity,
            }
            for item in order.items.select_related('product').order_by('id')
        ]
        _restore_current_stock(previous_line_items)

    if order is None:
        order = SaleOrder.objects.create(customer=customer, note=note or '')

    SaleOrder.objects.filter(pk=order.pk).update(
        customer=customer,
        note=note or '',
        created_at=order_datetime,
    )
    order.refresh_from_db()

    if action == 'update':
        order.items.all().delete()

    _consume_current_stock(line_items)

    created_sales = []
    for item in line_items:
        created_sale = Sale.objects.create(
            order=order,
            product=item['product'],
            customer=customer,
            quantity=item['quantity'],
            unit_price=item['unit_price'],
            payment_method=item['payment_method'],
        )
        created_sale.date = order_datetime
        created_sales.append(created_sale)

    if created_sales:
        Sale.objects.bulk_update(created_sales, ['date'])

    after_data = snapshot_sale_order(order)

    log_sale_order_change(
        order=order,
        order_id_snapshot=order.id,
        action=action,
        changed_by=changed_by,
        reason=reason,
        before_data=before_data,
        after_data=after_data,
    )
    return order


@transaction.atomic
def delete_sale_order_correction(*, order, changed_by, reason):
    before_data = snapshot_sale_order(order)
    order_id_snapshot = order.id
    previous_line_items = [
        {
            'product': item.product,
            'quantity': item.quantity,
        }
        for item in order.items.select_related('product').order_by('id')
    ]

    _restore_current_stock(previous_line_items)
    order.delete()

    log_sale_order_change(
        order=None,
        order_id_snapshot=order_id_snapshot,
        action='delete',
        changed_by=changed_by,
        reason=reason,
        before_data=before_data,
        after_data={},
    )
