"""The Pedido screen: pick products, add transport and fees, hand over a PDF.

Kept out of views.py, which is already thousands of lines, following the same
split as shelf_views.

Nothing is stored. A quote is a piece of paper the shop hands a reseller, and
the decision was taken deliberately: no model, no number, no history. The cost
is that a price quoted two weeks ago cannot be looked up here.

Two rules are worth knowing before changing this:

* The unit price defaults to the **wholesale** price, and a product without one
  arrives with an empty price that the form refuses to submit. Falling back to
  the retail price would quietly send a retail number to a reseller, which is
  worse than making somebody type it.
* Products with no stock can be quoted. A reseller orders what is on the way,
  so the product search runs with ``scope=stock`` (the whole catalogue) and each
  line prints its availability instead of being hidden.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import PrintProfile, Product
from .permissions import manager_required
from .services import pedido as pedido_math
from .services import pedido_pdf
from .services import shopify_sync

DEFAULT_VALID_DAYS = 7
MAX_LINES = 200


def _decimal(raw, default=Decimal('0.00')):
    raw = (raw or '').strip().replace(',', '.')
    if not raw:
        return default
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return default


def _int(raw, default=0):
    try:
        return int((raw or '').strip())
    except (TypeError, ValueError):
        return default


def _product_title(product):
    """Perfumes read as "LATTAFA Khamrah Waha EDP 100ml" - the same naming the
    customer catalogue and the Shopify listings use. Everything else keeps
    display_name, which carries the spec and colour an accessory needs."""
    if shopify_sync.is_perfume(product):
        return product.title_parts()
    return product.display_name


def _availability_label(product, on_order_ids):
    """Reuse the catalogue's own wording so a quote and the product list never
    describe the same stock differently."""
    from .views import (PT_AVAILABILITY, get_catalog_availability_parts)
    _label, state = get_catalog_availability_parts(
        int(product.total_stock() or 0), on_order=product.id in on_order_ids)
    return PT_AVAILABILITY.get(state, '')


def _read_lines(request):
    """Turn the posted parallel arrays into lines, plus any complaint.

    A row with no product is an empty spare row on the form and is dropped
    silently. A row with a product but no price is an error: that is the
    product with no wholesale price, and guessing is the one thing not allowed.
    """
    product_ids = request.POST.getlist('product_id')[:MAX_LINES]
    quantities = request.POST.getlist('quantity')
    unit_prices = request.POST.getlist('unit_price')

    wanted = []
    for index, raw_id in enumerate(product_ids):
        if not (raw_id or '').strip().isdigit():
            continue
        wanted.append((int(raw_id),
                       _int(quantities[index] if index < len(quantities) else '', 0),
                       (unit_prices[index] if index < len(unit_prices) else '')))

    products = {p.id: p for p in Product.objects.filter(
        id__in=[pid for pid, _, _ in wanted])}
    on_order_ids = _on_order_ids()

    lines, errors = [], []
    for product_id, quantity, raw_price in wanted:
        product = products.get(product_id)
        if product is None:
            continue
        if quantity < 1:
            errors.append(f'{product.display_name}: indique uma quantidade.')
            continue
        if not (raw_price or '').strip():
            errors.append(f'{product.display_name}: sem preço grossista, preencha o preço.')
            continue
        unit_price = _decimal(raw_price)
        if unit_price <= 0:
            errors.append(f'{product.display_name}: o preço tem de ser maior que zero.')
            continue
        lines.append({
            'product': product,
            'title': _product_title(product),
            'barcode': product.barcode or '',
            'availability': _availability_label(product, on_order_ids),
            'quantity': quantity,
            'unit_price': pedido_math.money(unit_price),
            'line_total': pedido_math.line_total(quantity, unit_price),
        })
    return lines, errors


def _on_order_ids():
    from .views import product_ids_on_order
    return product_ids_on_order()


def _read_extras(request):
    labels = request.POST.getlist('extra_label')
    amounts = request.POST.getlist('extra_amount')
    extras = []
    for index, label in enumerate(labels):
        raw_amount = amounts[index] if index < len(amounts) else ''
        if not (label or '').strip() and not (raw_amount or '').strip():
            continue
        extras.append({'label': label, 'amount': _decimal(raw_amount)})
    return extras


def _read_customer(request):
    """Exactly the three fields the form shows, and nothing else.

    Picking a customer fills those fields in the browser; the server does not
    re-read the record afterwards. If it did, an address typed or corrected on
    the form would be silently replaced by the stored one - and a delivery
    address is the field most likely to be missing or wrong in the record.

    The NIF is never carried through at all: most customers here never gave a
    usable one, so it would be a blank line on the paper more often than not.
    The phone is what the shop reaches them on.
    """
    return {'name': (request.POST.get('customer_name') or '').strip(),
            'phone': (request.POST.get('customer_phone') or '').strip(),
            'address': (request.POST.get('customer_address') or '').strip()}


@login_required
@manager_required
def pedido_view(request):
    if request.method != 'POST':
        return render(request, 'stock/pedido.html', {
            'default_valid_days': DEFAULT_VALID_DAYS,
            'errors': [],
        })

    lines, errors = _read_lines(request)
    if not lines and not errors:
        errors.append('Adicione pelo menos um produto.')
    if errors:
        return render(request, 'stock/pedido.html', {
            'default_valid_days': DEFAULT_VALID_DAYS,
            'errors': errors,
        }, status=400)

    discount_kind = (request.POST.get('discount_kind') or '').strip()
    if discount_kind not in pedido_math.DISCOUNT_KINDS:
        discount_kind = pedido_math.AMOUNT

    totals = pedido_math.compute_totals(
        lines,
        shipping=_decimal(request.POST.get('shipping')),
        extras=_read_extras(request),
        discount_value=_decimal(request.POST.get('discount_value')),
        discount_kind=discount_kind,
    )

    today = timezone.localdate()
    valid_days = _int(request.POST.get('valid_days'), DEFAULT_VALID_DAYS)
    valid_days = min(max(valid_days, 0), 365)
    valid_until = today + timedelta(days=valid_days) if valid_days else None

    from .views import resolve_active_store
    active_store, _store_is_all = resolve_active_store(request)
    profile = PrintProfile.get_for_store(active_store)
    customer = _read_customer(request)

    pdf_bytes = pedido_pdf.build_pedido_pdf(
        profile=profile,
        lines=lines,
        totals=totals,
        date_label=today.strftime('%d/%m/%Y'),
        valid_until_label=valid_until.strftime('%d/%m/%Y') if valid_until else '',
        customer_name=customer['name'],
        customer_phone=customer['phone'],
        customer_address=customer['address'],
        note=(request.POST.get('note') or '').strip(),
        payment_info=getattr(profile, 'payment_info', '') or '',
    )

    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    who = ''.join(ch for ch in customer['name'] if ch.isalnum())[:20]
    filename = f"pedido-{who or 'cliente'}-{stamp}.pdf"
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
