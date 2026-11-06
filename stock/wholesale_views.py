"""The wholesale catalogue, walked through as a customer would.

Staff only for now: a shop login stands in for a customer account until the
customer portal exists, and "Ver como" picks whose prices to see. The pages speak
Portuguese because that is who they are for.
"""
import json
from collections import Counter

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import OrderRequestForm
from .models import Brand, CartLine, Customer, OrderRequest, Product, ProductType, SaleOrder
from .permissions import manager_required
from .services import submissions
from .services.wholesale_cart import add_to_cart, cart_quote, set_quantity, submit_request
from .services.wholesale_catalog import PAGE_SIZE, brand_sort_key, catalog_rows
from .services.wholesale_discount import last_discount
from .services.wholesale_orders import cancel_order, reopen_in_cart, shop_phone
from .services.wholesale_picks import perfume_choices

SESSION_KEY = 'wholesale_customer'
EDITING_KEY = 'wholesale_editing'


def _customer(request):
    """The customer being previewed, with the discount the rest of the shop would
    give them: set by hand or from their last sale, whichever is newer."""
    pk = request.session.get(SESSION_KEY)
    customer = Customer.objects.filter(pk=pk, kind=Customer.REVENDA).first() if pk else None
    if customer is not None:
        agreed = last_discount(customer.pk)
        customer.portal_discount = agreed['discount'] if agreed else customer.wholesale_discount
    return customer


def brand_tabs(counts):
    names = sorted(counts, key=lambda name: brand_sort_key(name, counts[name]))
    logos = {b.name.lower(): b for b in Brand.objects.filter(name__in=names)}
    return [{'name': name, 'n': counts[name], 'brand': logos.get(name.lower())} for name in names]


def _number(value):
    text = (value or '').strip()
    return int(text) if text.lstrip('-').isdigit() else None


def _shared(request, customer):
    quote = cart_quote(request.user, customer, {})
    return {
        'customer': customer,
        'customers': Customer.objects.filter(kind=Customer.REVENDA).order_by('name').only('id', 'name'),
        'bar': quote,
    }


@login_required
@manager_required
def catalog_view(request):
    query = request.GET.get('q', '').strip()
    brand = request.GET.get('brand', '').strip()
    kind = request.GET.get('type', '').strip()
    gender = request.GET.get('gender', '').strip()
    # Browsing shows what is on sale; a search also finds what has sold out,
    # since that is how someone gets to ask for it.
    stock = request.GET.get('stock')
    show_all = stock == 'all' or (bool(query) and stock != 'in')
    customer = _customer(request)
    # The tabs count what the rest of the page allows, so each row of filters is
    # worked out from the list before it.
    everything = catalog_rows(query=query, gender=gender, show_all=show_all, viewer=request.user, customer=customer)
    by_type = Counter(row['type_code'] for row in everything)
    rows = [row for row in everything if not kind or row['type_code'] == kind]
    by_brand = Counter(row['brand'] for row in rows)
    if brand:
        rows = [row for row in rows if row['brand'].lower() == brand.lower()]
    page_obj = Paginator(rows, PAGE_SIZE).get_page(request.GET.get('page'))
    return render(request, 'stock/catalog.html', {
        'tab': 'catalog',
        'page_obj': page_obj,
        'total': len(rows),
        'query': query,
        'brand': brand,
        'type': kind,
        'gender': gender,
        'show_all': show_all,
        'type_total': sum(by_type.values()),
        'type_tabs': [{'value': t.code, 'label': t.customer_label or t.name, 'n': by_type[t.code]}
                      for t in ProductType.all_cached() if by_type.get(t.code)],
        'brand_total': sum(by_brand.values()),
        'brand_tabs': brand_tabs(by_brand),
        'genders': [{'value': value, 'label': label} for value, label in Product.GENDER_SHOPIFY_TAGS.items()],
        **_shared(request, customer),
    })


@login_required
@manager_required
@require_POST
def choose_customer(request):
    pk = _number(request.POST.get('customer'))
    if pk and Customer.objects.filter(pk=pk, kind=Customer.REVENDA).exists():
        request.session[SESSION_KEY] = pk
    else:
        request.session.pop(SESSION_KEY, None)
    from .views import _back_to
    return _back_to(request, 'catalog')


def _picks(text):
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


@login_required
@manager_required
def pick_search(request):
    return JsonResponse({'results': perfume_choices(request.GET.get('q'))})


def _wants_json(request):
    return request.headers.get('X-Requested-With') == 'fetch'


@login_required
@manager_required
@require_POST
def cart_add(request):
    product = get_object_or_404(Product, pk=_number(request.POST.get('product')) or 0)
    customer = _customer(request)
    try:
        add_to_cart(request.user, product, _number(request.POST.get('quantity')), customer,
                    picks=_picks(request.POST.get('picks')), replace=request.POST.get('replace') == '1')
    except ValueError as error:
        if _wants_json(request):
            return JsonResponse({'error': str(error)}, status=400)
        messages.error(request, str(error))
    else:
        if _wants_json(request):
            quote = cart_quote(request.user, customer, {})
            line = next((l for l in quote['lines'] if l['product_id'] == product.pk), None)
            return JsonResponse({'units': quote['units'], 'in_cart': line['quantity'] if line else 0,
                                 'subtotal': f"{quote['subtotal']:.2f}", 'message': quote['progress']['message'],
                                 'url': reverse('wholesale_cart')})
    from .views import _back_to
    return _back_to(request, 'catalog')


@login_required
@manager_required
@require_POST
def cart_update(request):
    quantity = _number(request.POST.get('quantity'))
    product_id = _number(request.POST.get('product'))
    try:
        if quantity is None or product_id is None:
            raise ValueError('Indique uma quantidade v\u00e1lida.')
        set_quantity(request.user, product_id, quantity)
    except ValueError as error:
        messages.error(request, str(error))
    return redirect('wholesale_cart')


def _destination(data):
    return {'delivery': data.get('delivery') or 'delivery', 'country': data.get('country') or 'PT',
            'postal_code': data.get('postal_code') or ''}


def _initial(customer):
    if customer is None:
        return {'delivery': 'delivery', 'country': 'PT'}
    return {'delivery': 'delivery', 'country': 'PT', 'contact_name': customer.name, 'phone': customer.phone or '',
            'email': customer.email or '', 'address': customer.address or ''}


def _editing(request):
    """The order this cart is changing. Once the shop has confirmed it the changes
    are dropped: left in the cart, they would go out as a second order."""
    public_id = request.session.get(EDITING_KEY)
    if not public_id:
        return None
    order = OrderRequest.objects.filter(public_id=public_id).first()
    if order is not None and order.customer_can_change:
        return order
    request.session.pop(EDITING_KEY, None)
    CartLine.objects.filter(user=request.user).delete()
    messages.error(request, 'Este pedido j\u00e1 foi confirmado e n\u00e3o pode ser alterado.')
    return None


def _cart_page(request, customer, form, status=200):
    data = form.data if form.is_bound else form.initial
    editing = _editing(request)     # first: it can empty the cart
    return render(request, 'stock/wholesale_cart.html', {
        'tab': 'cart', 'form': form, 'quote': cart_quote(request.user, customer, _destination(data)),
        'editing': editing,
        **_shared(request, customer),
    }, status=status)


@login_required
@manager_required
def cart_view(request):
    customer = _customer(request)
    return _cart_page(request, customer, OrderRequestForm(initial=_initial(customer)))


@login_required
@manager_required
def cart_summary(request):
    """The totals again for a new destination: the page shows what the server says."""
    quote = cart_quote(request.user, _customer(request), _destination(request.GET))
    return render(request, 'stock/_wholesale_summary.html', {'quote': quote})


@login_required
@manager_required
@require_POST
def cart_submit(request):
    customer = _customer(request)
    form = OrderRequestForm(request.POST)
    if not form.is_valid():
        return _cart_page(request, customer, form)
    data = {**form.cleaned_data, **_destination(form.cleaned_data)}
    try:
        with transaction.atomic():
            submissions.claim(request)
            order = submit_request(request.user, customer, data, request.POST.get('fingerprint') or '',
                                   editing=request.session.get(EDITING_KEY))
    except submissions.AlreadySubmitted:
        return redirect('wholesale_orders')
    except ValueError as error:
        messages.error(request, str(error))
        return _cart_page(request, customer, form)
    request.session.pop(EDITING_KEY, None)
    return redirect('wholesale_order', public_id=order.public_id)


def _visible_orders(request, customer):
    """A customer sees their own requests; with none chosen, the login's own."""
    rows = OrderRequest.objects.prefetch_related('lines')
    return rows.filter(customer=customer) if customer else rows.filter(submitted_by=request.user)


def _mine(request, public_id):
    customer = _customer(request)
    wanted = OrderRequest.objects.prefetch_related('lines').filter(
        Q(submitted_by=request.user) | Q(customer=customer) if customer else Q(submitted_by=request.user))
    return get_object_or_404(wanted, public_id=public_id)


@login_required
@manager_required
def orders_view(request):
    customer = _customer(request)
    sales = (SaleOrder.objects.filter(customer=customer).prefetch_related('items').order_by('-created_at')[:100]
             if customer else [])
    return render(request, 'stock/wholesale_orders.html', {
        'tab': 'orders', 'orders': _visible_orders(request, customer), 'sales': sales,
        **_shared(request, customer),
    })


@login_required
@manager_required
def sale_view(request, pk):
    """One of the customer's past orders: what they bought, nothing of the shop's."""
    customer = _customer(request)
    if customer is None:
        raise Http404
    order = get_object_or_404(SaleOrder.objects.prefetch_related('items__product'), pk=pk, customer=customer)
    lines = [{'product': item.product, 'quantity': item.quantity, 'unit_price': item.unit_price,
              'amount': item.quantity * item.unit_price} for item in order.items.all()]
    return render(request, 'stock/wholesale_sale.html', {'tab': 'orders', 'order': order, 'lines': lines,
                                                         **_shared(request, customer)})


@login_required
@manager_required
def order_view(request, public_id):
    return render(request, 'stock/wholesale_order.html', {'tab': 'orders', 'order': _mine(request, public_id),
                                                          'shop_phone': shop_phone(),
                                                          **_shared(request, _customer(request))})


@login_required
@manager_required
@require_POST
def order_edit(request, public_id):
    order = _mine(request, public_id)
    if not order.customer_can_change:
        messages.error(request, 'Este pedido j\u00e1 foi confirmado e n\u00e3o pode ser alterado.')
        return redirect('wholesale_order', public_id=order.public_id)
    reopen_in_cart(order, request.user)
    request.session[EDITING_KEY] = str(order.public_id)
    return redirect('wholesale_cart')


@login_required
@manager_required
@require_POST
def order_edit_stop(request):
    """Leave the order as it was sent, and the cart empty."""
    if request.session.pop(EDITING_KEY, None):
        CartLine.objects.filter(user=request.user).delete()
    return redirect('wholesale_orders')


@login_required
@manager_required
@require_POST
def order_cancel(request, public_id):
    order = _mine(request, public_id)
    if cancel_order(order, request.user, OrderRequest.CUSTOMER_CAN_CANCEL):
        request.session.pop(EDITING_KEY, None)
        messages.success(request, 'Pedido cancelado.')
    return redirect('wholesale_order', public_id=order.public_id)


@login_required
@manager_required
def order_requests(request):
    """What customers asked for: the shop's side."""
    if request.method == 'POST':
        order = OrderRequest.objects.filter(pk=_number(request.POST.get('id')) or 0).first()
        if order is None:
            messages.error(request, 'That request no longer exists.')
        elif request.POST.get('action') == 'cancel' and cancel_order(order, request.user, OrderRequest.SHOP_CAN_CANCEL):
            messages.success(request, f'Request #{order.id} cancelled.')
        return redirect('order_requests')
    orders = (OrderRequest.objects.select_related('customer', 'submitted_by', 'pedido').prefetch_related('lines'))
    return render(request, 'stock/order_requests.html', {'orders': orders})
