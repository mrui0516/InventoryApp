"""An order after it is sent.

It waits in the shop's Pedido as a draft. Downloading that draft's PDF is the
shop confirming the stock, and closes the order to the customer. Selling the
draft completes it. The shop can cancel until then; the customer until confirmed.
The stock moves only at the sale, as for any Pedido.
"""
from decimal import Decimal

from django.utils import timezone

from ..models import (BusinessSettings, CartLine, OrderRequest, OrderRequestLine, PedidoDraft, PrintProfile,
                      Product, Store)
from . import pedido as pedido_math
from .wholesale_catalog import display_title

TIMES = '×'


def shop_store():
    """The shop taking wholesale orders (Settings -> Shop settings), else the default one."""
    chosen = BusinessSettings.get().wholesale_store
    if chosen is not None and chosen.is_active:
        return chosen
    return Store.objects.filter(is_default=True).first() or Store.objects.filter(is_active=True).first()


def shop_phone():
    store = shop_store()
    return PrintProfile.get_for_store(store).phone if store else ''


def _address(order):
    if order.delivery == 'pickup':
        return 'Levantamento em loja'
    place = f'{order.postal_code} {order.city}'.strip()
    if order.delivery == 'point':
        return f'Ponto de recolha perto de {place}'
    return f'{order.address}\n{place}'.strip()


def _note(order):
    """What the shop reads on the quote: the customer's note, then each choice."""
    parts = [order.note] if order.note else []
    for line in order.lines.all():
        if line.picks:
            chosen = ', '.join(f"{pick['quantity']} {TIMES} {pick['name']}" for pick in line.picks)
            parts.append(f'{line.name}: {chosen}')
    return '\n'.join(parts)


def save_to_pedido(order):
    """The order as a draft of the main shop's Pedido, new or brought up to date."""
    store = shop_store()
    if store is None:
        return
    draft = order.pedido or PedidoDraft(store=store, created_by=order.submitted_by)
    draft.customer = order.customer
    draft.customer_name = (order.contact_name or order.business_name)[:100]
    draft.customer_phone = order.phone[:40]
    draft.customer_address = _address(order)
    draft.lines = [{'product_id': line.product_id, 'quantity': line.quantity, 'unit_price': str(line.final_price)}
                   for line in order.lines.all()]
    draft.shipping = order.shipping_fee or Decimal('0')
    draft.note = _note(order)
    draft.goods_total = order.goods_total
    draft.total = order.total
    draft.save()
    if order.pedido_id != draft.pk:
        order.pedido = draft
        order.save(update_fields=['pedido'])


def _follows_quote(order, draft):
    """The shop may have changed the quote before printing it. What it printed is
    what was agreed, so that is what the customer's order now says."""
    ordered = sorted((line.product_id, line.quantity, line.final_price) for line in order.lines.all())
    quoted = sorted((line['product_id'], int(line['quantity']), pedido_math.money(line['unit_price']))
                    for line in draft.lines)
    if ordered == quoted and draft.total == order.total:
        return
    old = {line.product_id: line for line in order.lines.all()}
    products = {product.pk: product for product in Product.objects.filter(pk__in=[row[0] for row in quoted])}
    rows = []
    for product_id, quantity, price in quoted:
        before, product = old.get(product_id), products.get(product_id)
        rows.append(OrderRequestLine(
            request=order, product_id=product_id, quantity=quantity, picks=before.picks if before else [],
            name=before.name if before else f'{product.brand} {display_title(product)}'.strip(),
            barcode=before.barcode if before else product.barcode, base_price=price,
            customer_discount=Decimal('0'), catalog_price=price, tier_discount=Decimal('0'), final_price=price,
            line_total=price * quantity))
    order.lines.all().delete()
    OrderRequestLine.objects.bulk_create(rows)
    goods = draft.total - draft.shipping
    order.subtotal = draft.goods_total
    order.tier_discount = Decimal('0')
    order.tier_total = max(draft.goods_total - goods, Decimal('0'))
    order.goods_total = goods
    order.shipping_fee = draft.shipping
    if draft.shipping > 0:
        order.shipping_state = 'standard'
    elif order.shipping_state == 'standard':
        order.shipping_state = 'free'


def confirm_from_pedido(draft):
    """The PDF was downloaded: the stock is confirmed, and printed again it stays so."""
    order = OrderRequest.objects.filter(pedido=draft, status__in=OrderRequest.SHOP_CAN_CANCEL).first()
    if order is None:
        return
    _follows_quote(order, draft)
    if order.status == OrderRequest.SUBMITTED:
        order.status = OrderRequest.CONFIRMED
        order.confirmed_at = timezone.now()
    order.save()


def complete_from_sale(draft, sale):
    OrderRequest.objects.filter(pedido=draft, status__in=OrderRequest.SHOP_CAN_CANCEL).update(
        status=OrderRequest.COMPLETED, completed_at=timezone.now(), sale_order=sale)


def cancel_order(order, user, allowed):
    """``allowed``: the statuses this person may cancel from. The check is in the
    update, so the shop confirming at the same moment wins or loses cleanly."""
    done = OrderRequest.objects.filter(pk=order.pk, status__in=allowed).update(
        status=OrderRequest.CANCELLED, cancelled_at=timezone.now(), cancelled_by=user)
    if done:
        PedidoDraft.objects.filter(pk=order.pedido_id, status=PedidoDraft.OPEN).delete()
    return bool(done)


def cancel_for_deleted_draft(draft, user):
    for order in OrderRequest.objects.filter(pedido=draft, status__in=OrderRequest.SHOP_CAN_CANCEL):
        OrderRequest.objects.filter(pk=order.pk).update(status=OrderRequest.CANCELLED, cancelled_at=timezone.now(),
                                                        cancelled_by=user)


def reopen_in_cart(order, user):
    """The order's lines, back in the cart to be changed and sent again."""
    CartLine.objects.filter(user=user).delete()
    CartLine.objects.bulk_create([CartLine(user=user, product_id=line.product_id, quantity=line.quantity,
                                           picks=line.picks) for line in order.lines.all() if line.product_id])
