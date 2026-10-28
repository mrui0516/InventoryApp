"""Side panels: a record's essentials, opened over the list it was tapped in.

Each view returns a fragment with three parts - ``title``, ``body``, ``foot`` -
that base.html's side panel puts in place (the same parts a row ``<template>``
carries). The list underneath never reloads, so its page, filters and scroll
stay as they were. Every panel checks the same access as the full page it
summarises, and the full page stays one tap away in the panel's footer.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import DecimalField, ExpressionWrapper, F, IntegerField, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import ARInvoice, Customer, InboundOrder, Product, Purchase, Sale, SaleOrder, Supplier
from .permissions import has_manager_access, has_sales_sensitive_access, manager_required
from .stores import resolve_active_store

MONEY = DecimalField(max_digits=12, decimal_places=2)
LINE_VALUE = ExpressionWrapper(F('items__unit_price') * F('items__quantity'), output_field=MONEY)


@login_required
def customer_panel(request, customer_id):
    customer = get_object_or_404(Customer, pk=customer_id)
    active_store, store_is_all = resolve_active_store(request)
    store_q = Q() if (store_is_all or active_store is None) else Q(store=active_store)
    show_sensitive = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)

    orders = SaleOrder.objects.filter(store_q, customer=customer)
    recent = list(orders.select_related('store').order_by('-created_at')
                  .annotate(qty=Coalesce(Sum('items__quantity'), 0, output_field=IntegerField()),
                            amount=Coalesce(Sum(LINE_VALUE), Decimal('0.00'), output_field=MONEY))[:5])
    lines = Sale.objects.filter(store_q, order__customer=customer)
    spent = lines.aggregate(t=Sum(F('unit_price') * F('quantity'), output_field=MONEY))['t'] or Decimal('0.00')
    top = list(lines.values('product_id').annotate(qty=Sum('quantity')).order_by('-qty')[:3])
    names = {p.id: p.display_name for p in Product.all_objects.filter(id__in=[t['product_id'] for t in top])}
    for t in top:
        t['name'] = names.get(t['product_id'], '?')
    balance = (ARInvoice.objects.filter(store_q, customer=customer)
               .aggregate(b=Sum(F('total_amount') - F('amount_paid'), output_field=MONEY))['b'] or Decimal('0.00'))

    return render(request, 'stock/panels/customer.html', {
        'customer': customer,
        'order_count': orders.count(),
        'last_order': recent[0] if recent else None,
        'recent': recent,
        'top': top,
        'spent': spent,
        'balance': balance,
        'show_sensitive': show_sensitive,
        'show_sales_sensitive': show_sales_sensitive,
        'store_is_all': store_is_all,
    })


@login_required
def product_panel(request, pk):
    from .services.low_stock import in_transit_quantities
    from .views import get_product_image_url
    product = get_object_or_404(Product.objects.select_related('category').prefetch_related('images'), pk=pk)
    show_sensitive = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)

    batches = Purchase.objects.filter(product=product, remaining__gt=0).order_by('date')
    stock = batches.aggregate(s=Sum('remaining'))['s'] or 0
    first_batch = batches.first()
    sales = Sale.objects.filter(product=product)
    since = timezone.now() - timedelta(days=30)
    context = {
        'product': product,
        'image_url': get_product_image_url(product),
        'stock': stock,
        'in_transit': in_transit_quantities([product.id]).get(product.id, 0),
        'show_sensitive': show_sensitive,
        'show_sales_sensitive': show_sales_sensitive,
    }
    if show_sensitive:
        context['fifo_cost'] = first_batch.cost_price if first_batch else None
    if show_sales_sensitive:
        context.update({
            'sold_total': sales.aggregate(q=Sum('quantity'))['q'] or 0,
            'sold_30d': sales.filter(date__gte=since).aggregate(q=Sum('quantity'))['q'] or 0,
            'recent_sales': list(sales.select_related('order__customer').order_by('-date')[:5]),
        })
    return render(request, 'stock/panels/product.html', context)


@login_required
@manager_required
def supplier_panel(request, supplier_id):
    from .services.lead_time import supplier_lead_stats
    supplier = get_object_or_404(Supplier, pk=supplier_id)
    orders = InboundOrder.objects.filter(supplier=supplier)
    received = orders.filter(status='received')
    pending = list(orders.filter(status='pending_receipt').order_by('-created_at')
                   .annotate(units=Coalesce(Sum('pending_items__quantity'), 0)))
    recent = list(received.order_by('-received_at', '-id')
                  .annotate(units=Coalesce(Sum('items__quantity'), 0))[:5])
    return render(request, 'stock/panels/supplier.html', {
        'supplier': supplier,
        'received_count': received.count(),
        'bought': received.aggregate(t=Sum('total_amount'))['t'] or Decimal('0.00'),
        'lead': supplier_lead_stats(supplier),
        'pending': pending,
        'recent': recent,
    })
