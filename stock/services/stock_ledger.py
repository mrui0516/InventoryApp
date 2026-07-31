"""Read-only reconstructed stock ledger for a single product (Layer 1).

Merges the three tables that *should* explain every change to on-hand quantity —
purchase batches (+), sale lines (−, honouring ``SaleOrder.affects_stock``) and
manual ``StockAdjustmentLog`` entries (±) — into one chronological journal with a
running balance. Comparing that reconstructed balance against the real on-hand
(Σ ``Purchase.remaining``) surfaces any quantity that leaked through an *untracked*
path — e.g. editing a batch's ``quantity`` on the inbound-order edit page, or a
deleted row — because those never leave a movement/log behind.

No schema change: this reconstructs from existing rows. Per-batch attribution of
each sale (which batch it consumed) is a forward-only ledger and is NOT available
retroactively, so this journal is product-level.

Double-count guard: ``api_adjust_total_stock`` *increases* stock by creating a new
Purchase batch (which already appears as a ``+qty`` row) and *also* writes a
``total_stock`` log — so that log's delta is excluded from the balance here.
A ``total_stock`` *decrease* consumes via FIFO (no batch row), so its log delta
IS the only representation and must be counted. ``purchase_remaining`` edits are
always counted (a manual remaining edit has no other event).
"""
from django.utils import timezone

from ..models import Purchase, Sale, StockAdjustmentLog


def build_stock_ledger(product):
    events = []
    pay_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    for p in product.purchase_set.select_related('supplier', 'inbound_order').all():
        events.append({
            'at': p.date,
            'kind': 'purchase',
            'rank': 0,
            'id': p.id,
            'label': f'Purchase batch #{p.id}',
            'ref': (f'Inbound #{p.inbound_order_id}' if p.inbound_order_id else 'Direct purchase'),
            'who': p.supplier.name if p.supplier_id else '',
            'in_qty': p.quantity,
            'out_qty': 0,
            'delta': p.quantity,
            'cost_price': p.cost_price,
            'batch_remaining': p.remaining,
            'note': '',
            'no_stock': False,
            # merged sales/stock columns
            'store': '',
            'party': p.supplier.name if p.supplier_id else '',
            'unit_price': p.cost_price,
            'line_total': (p.cost_price * p.quantity) if p.cost_price is not None else None,
            'payment': '',
            'profit': None,
            'order_id': None,
            'customer_id': None,
        })

    sales = (
        Sale.objects
        .filter(product=product)
        .select_related('order', 'order__store', 'order__customer', 'customer', 'store')
        .all()
    )
    for s in sales:
        order = s.order
        affects = order.affects_stock if order else True
        at = order.created_at if (order and order.created_at) else s.date
        cust = order.customer if (order and order.customer_id) else s.customer
        store = order.store if (order and order.store_id) else s.store
        line_total = (s.unit_price or 0) * s.quantity
        events.append({
            'at': at,
            'kind': 'sale',
            'rank': 1,
            'id': s.id,
            'label': (f'Order #{order.id}' if order else f'Legacy sale #{s.id}'),
            'ref': cust.name if cust else 'Walk-in / No customer',
            'who': '',
            'in_qty': 0,
            'out_qty': (s.quantity if affects else 0),
            'delta': (-s.quantity if affects else 0),
            'cost_price': s.unit_price,
            'batch_remaining': None,
            'note': ('' if affects else 'Recorded — did NOT affect stock'),
            'no_stock': not affects,
            # merged sales/stock columns
            'store': store.name if store else '',
            'party': cust.name if cust else 'Walk-in / No customer',
            'unit_price': s.unit_price,
            'line_total': line_total,
            'payment': pay_labels.get(s.payment_method, (s.payment_method or 'Other').title()),
            'profit': None,  # attached by the caller when profit is shown
            'order_id': order.id if order else None,
            'customer_id': cust.id if cust else None,
        })

    for a in (
        StockAdjustmentLog.objects
        .filter(product=product)
        .select_related('user', 'purchase')
        .all()
    ):
        raw_delta = a.new_value - a.old_value
        created_batch = (a.adjustment_type == 'total_stock' and raw_delta > 0)
        # A total-stock *increase* is represented by a created batch row; don't
        # double-count it in the balance (delta 0), but still show the audit row.
        balance_delta = 0 if created_batch else raw_delta
        events.append({
            'at': a.created_at,
            'kind': 'adjust',
            'rank': 2,
            'id': a.id,
            'label': f'Manual adjust · {a.get_adjustment_type_display()}',
            'ref': (f'Batch #{a.purchase_id}' if a.purchase_id else 'Total stock'),
            'who': a.user.get_username() if a.user_id else '',
            'in_qty': (balance_delta if balance_delta > 0 else 0),
            'out_qty': (-balance_delta if balance_delta < 0 else 0),
            'delta': balance_delta,
            'cost_price': None,
            'batch_remaining': None,
            'note': (f'{a.old_value} → {a.new_value}'
                     + (' (created batch)' if created_batch else '')),
            'no_stock': created_batch,
            # merged sales/stock columns
            'store': '',
            'party': a.user.get_username() if a.user_id else '',
            'unit_price': None,
            'line_total': None,
            'payment': '',
            'profit': None,
            'order_id': None,
            'customer_id': None,
        })

    now = timezone.now()
    events.sort(key=lambda e: (e['at'] or now, e['rank'], e['id']))

    balance = 0
    for e in events:
        balance += e['delta']
        e['balance'] = balance

    reconstructed = balance
    actual_onhand = sum(p.remaining for p in product.purchase_set.all())
    difference = actual_onhand - reconstructed  # 0 => every unit is accounted for

    return {
        'events': list(reversed(events)),  # newest first for display
        'reconstructed_balance': reconstructed,
        'actual_onhand': actual_onhand,
        'difference': difference,
        'unexplained': abs(difference),
        'has_discrepancy': difference != 0,
        'event_count': len(events),
    }
