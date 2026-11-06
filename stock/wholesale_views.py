"""The wholesale catalogue, walked through as a customer would.

Staff only for now: a shop login stands in for a customer account until the
customer portal exists, and "Ver como" picks whose prices to see. The pages speak
Portuguese because that is who they are for.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import OrderRequestForm
from .models import Customer, OrderRequest, Product
from .permissions import manager_required
from .services import submissions
from .services.wholesale_cart import add_to_cart, cart_quote, set_quantity, submit_request
from .services.wholesale_catalog import PAGE_SIZE, catalog_options, catalog_rows

SESSION_KEY = 'wholesale_customer'


def _customer(request):
    pk = request.session.get(SESSION_KEY)
    return Customer.objects.filter(pk=pk, kind=Customer.REVENDA).first() if pk else None


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
    rows = catalog_rows(query=query, brand=brand, kind=kind, gender=gender, show_all=show_all,
                        viewer=request.user, customer=customer)
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
        'filters_on': bool(brand or kind or gender),
        **catalog_options(),
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


def _wants_json(request):
    return request.headers.get('X-Requested-With') == 'fetch'


@login_required
@manager_required
@require_POST
def cart_add(request):
    product = get_object_or_404(Product, pk=_number(request.POST.get('product')) or 0)
    customer = _customer(request)
    try:
        add_to_cart(request.user, product, _number(request.POST.get('quantity')), customer)
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
    return {'delivery': 'delivery', 'country': 'PT', 'business_name': customer.name, 'phone': customer.phone or '',
            'email': customer.email or '', 'address': customer.address or ''}


def _cart_page(request, customer, form, status=200):
    data = form.data if form.is_bound else form.initial
    return render(request, 'stock/wholesale_cart.html', {
        'tab': 'cart', 'form': form, 'quote': cart_quote(request.user, customer, _destination(data)),
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
            order = submit_request(request.user, customer, data, request.POST.get('fingerprint') or '')
    except submissions.AlreadySubmitted:
        return redirect('wholesale_orders')
    except ValueError as error:
        messages.error(request, str(error))
        return _cart_page(request, customer, form)
    return redirect('wholesale_order', public_id=order.public_id)


def _mine(request, public_id):
    return get_object_or_404(OrderRequest.objects.prefetch_related('lines'),
                             public_id=public_id, submitted_by=request.user)


@login_required
@manager_required
def orders_view(request):
    orders = OrderRequest.objects.filter(submitted_by=request.user).prefetch_related('lines')
    return render(request, 'stock/wholesale_orders.html', {'tab': 'orders', 'orders': orders,
                                                           **_shared(request, _customer(request))})


@login_required
@manager_required
def order_view(request, public_id):
    return render(request, 'stock/wholesale_order.html', {'tab': 'orders', 'order': _mine(request, public_id),
                                                          **_shared(request, _customer(request))})


def _cancel(order, user):
    updated = OrderRequest.objects.filter(pk=order.pk, status=OrderRequest.SUBMITTED).update(
        status=OrderRequest.CANCELLED, cancelled_at=timezone.now(), cancelled_by=user)
    return bool(updated)


@login_required
@manager_required
@require_POST
def order_cancel(request, public_id):
    order = _mine(request, public_id)
    if _cancel(order, request.user):
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
        elif request.POST.get('action') == 'cancel' and _cancel(order, request.user):
            messages.success(request, f'Request #{order.id} cancelled.')
        return redirect('order_requests')
    orders = (OrderRequest.objects.select_related('customer', 'submitted_by').prefetch_related('lines'))
    return render(request, 'stock/order_requests.html', {'orders': orders})
