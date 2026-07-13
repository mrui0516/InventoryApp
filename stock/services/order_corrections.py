from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import Sale, SaleOrder, SaleOrderChangeLog, SaleOrderPayment
from .stock_ops import consume_stock_fifo, restore_stock_fifo


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
        'payments': [
            {'method': payment.method, 'amount': str(payment.amount)}
            for payment in order.payments.all()
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
        consume_stock_fifo(product, quantity_needed)


def _restore_current_stock(line_items):
    for group in _summarise_quantities(line_items):
        product = group['product']
        quantity_to_restore = group['quantity']
        if quantity_to_restore <= 0:
            continue
        restore_stock_fifo(product, quantity_to_restore)


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
def save_sale_order_correction(*, order, customer, note, order_datetime, line_items, payment_totals, changed_by, reason, store=None):
    if not line_items:
        raise ValidationError("Add at least one product line before saving the order.")

    before_data = snapshot_sale_order(order)
    action = 'update' if order else 'create'
    previous_line_items = []

    # Keep an existing order's store on edit; a new order is attributed to the
    # active store (sales are per-store, inventory is shared).
    target_store = (order.store if (order and order.store_id) else None) or store

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
        order = SaleOrder.objects.create(customer=customer, note=note or '', store=target_store)

    SaleOrder.objects.filter(pk=order.pk).update(
        customer=customer,
        note=note or '',
        created_at=order_datetime,
        store=target_store,
    )
    order.refresh_from_db()

    if action == 'update':
        # Rebuild lines and the order-level payment records from scratch so a
        # removed product is fully rolled back (stock restored above, sale gone).
        order.items.all().delete()
        order.payments.all().delete()

    _consume_current_stock(line_items)

    created_sales = []
    for item in line_items:
        created_sale = Sale.objects.create(
            order=order,
            product=item['product'],
            customer=customer,
            store=target_store,
            quantity=item['quantity'],
            unit_price=item['unit_price'],
            payment_method=item['payment_method'],
        )
        created_sale.date = order_datetime
        created_sales.append(created_sale)

    if created_sales:
        Sale.objects.bulk_update(created_sales, ['date'])

    # Order-level split tender (authoritative record), mirroring the POS outbound.
    for method, amount in (payment_totals or {}).items():
        if amount and amount > 0:
            SaleOrderPayment.objects.create(order=order, method=method, amount=amount)

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
