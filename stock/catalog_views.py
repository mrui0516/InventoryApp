"""Settings -> Catalog: the lists behind every product, for admins."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import redirect, render

from .models import Category, Product, ProductType
from .permissions import admin_required
from .services.catalog_admin import delete_type, new_type_code

SWITCHES = ('takes_concentration', 'takes_volume', 'wholesale_discount', 'is_gift', 'shows_scent_details')


def _text(request, name, limit):
    return (request.POST.get(name) or '').strip()[:limit]


def _number(request, name):
    value = (request.POST.get(name) or '').strip()
    return int(value) if value.isdigit() else None


def _category(request):
    return Category.objects.filter(pk=_number(request, 'default_category') or 0).first()


@login_required
@admin_required
def catalog_settings(request):
    return redirect('catalog_types')


def _save_type(request, row):
    name, category = _text(request, 'name', 60), _category(request)
    if not name:
        messages.error(request, 'Give the type a name.')
        return
    if category is None:
        messages.error(request, 'Choose a category.')
        return
    if row is None:
        last = ProductType.objects.order_by('-sort_order').first()
        row = ProductType(code=new_type_code(name), sort_order=(last.sort_order if last else 0) + 10)
    row.name, row.default_category = name, category
    row.name_word = _text(request, 'name_word', 40)
    row.customer_label = _text(request, 'customer_label', 60)
    if request.POST.get('action') == 'add':
        row.save()
        messages.success(request, f'{row.name} added.')
        return
    for switch in SWITCHES:
        setattr(row, switch, request.POST.get(switch) == 'on')
    order = _number(request, 'sort_order')
    if order is not None:
        row.sort_order = order
    row.save()
    messages.success(request, f'{row.name} saved.')


@login_required
@admin_required
def catalog_types(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        row = ProductType.objects.filter(pk=_number(request, 'id') or 0).first()
        if action == 'add':
            _save_type(request, None)
        elif row is None:
            messages.error(request, 'That type no longer exists.')
        elif action == 'save':
            _save_type(request, row)
        elif action == 'delete':
            target = ProductType.objects.filter(pk=_number(request, 'move_to') or 0).first()
            try:
                moved = delete_type(row, target)
            except ValueError as error:
                messages.error(request, str(error))
            else:
                note = f' {moved} moved to {target.name}.' if moved else ''
                messages.success(request, f'{row.name} deleted.{note}')
        return redirect('catalog_types')

    live = dict(Product.objects.values_list('product_type').annotate(n=Count('id')).order_by())
    every = dict(Product.all_objects.values_list('product_type').annotate(n=Count('id')).order_by())
    types = list(ProductType.objects.select_related('default_category'))
    for row in types:
        row.live_count = live.get(row.code, 0)
        row.total_count = every.get(row.code, 0)
    return render(request, 'stock/catalog_types.html', {
        'tab': 'types', 'types': types, 'categories': Category.objects.order_by('name'),
    })
