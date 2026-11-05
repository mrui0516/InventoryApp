"""Settings -> Catalog: the lists behind every product, for admins."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse

from .models import Brand, Category, Product, ProductSeries, ProductType
from .permissions import admin_required
from .services.catalog_admin import (NameTaken, delete_type, merge_brands, merge_lines, new_type_code, rename_brand,
                                     rename_line)
from .services.catalog_prune import empty_brands, empty_series
from .services.names import title_case

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
        row = ProductType(code=new_type_code(name), sort_order=(last.sort_order if last else 0) + 10,
                          shows_scent_details=False, has_contents=True)
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


def _rename(request, rename, row, name):
    try:
        rename(row, name)
    except NameTaken as error:
        messages.error(request, f'{error} Merge them instead.')
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'{row.name} saved.')


def _merged(request, merge, keep, row):
    if keep is None:
        messages.error(request, 'Choose where to merge it.')
        return
    try:
        count = merge(keep, row)
    except ValueError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'{row.name} merged: {count} product{"s" if count != 1 else ""} moved to {keep.name}.')


@login_required
@admin_required
def catalog_brands(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        row = Brand.objects.filter(pk=_number(request, 'id') or 0).first()
        if action == 'add':
            name = title_case(_text(request, 'name', 50))
            if not name:
                messages.error(request, 'Give the brand a name.')
            elif Brand.objects.filter(name__iexact=name).exists():
                messages.error(request, f'{name} already exists.')
            else:
                Brand.objects.create(name=name)
                messages.success(request, f'{name} added.')
        elif row is None:
            messages.error(request, 'That brand no longer exists.')
        elif action == 'rename':
            _rename(request, rename_brand, row, _text(request, 'name', 50))
        elif action == 'merge':
            _merged(request, merge_brands, Brand.objects.filter(pk=_number(request, 'into') or 0).first(), row)
        elif action == 'delete':
            if empty_brands([row.pk]).exists():
                row.delete()
                messages.success(request, f'{row.name} deleted.')
            else:
                messages.error(request, f'{row.name} still has products.')
        return redirect('catalog_brands')

    brands = list(Brand.objects.annotate(
        live_count=Count('products', filter=Q(products__archived_at__isnull=True), distinct=True),
        line_count=Count('series', distinct=True)).order_by('name'))
    return render(request, 'stock/catalog_brands.html', {'tab': 'brands', 'brands': brands})


@login_required
@admin_required
def catalog_lines(request):
    chosen = _chosen_brand(request)
    if request.method == 'POST':
        action = request.POST.get('action')
        row = ProductSeries.objects.select_related('brand').filter(pk=_number(request, 'id') or 0).first()
        if action == 'add':
            brand = Brand.objects.filter(pk=_number(request, 'brand') or 0).first()
            name = title_case(_text(request, 'name', 100))
            if brand is None or not name:
                messages.error(request, 'Choose a brand and give the line a name.')
            elif brand.series.filter(name__iexact=name).exists():
                messages.error(request, f'{name} already exists.')
            else:
                ProductSeries.objects.create(brand=brand, name=name)
                messages.success(request, f'{name} added.')
        elif row is None:
            messages.error(request, 'That line no longer exists.')
        elif action == 'rename':
            _rename(request, rename_line, row, _text(request, 'name', 100))
        elif action == 'merge':
            _merged(request, merge_lines, ProductSeries.objects.filter(pk=_number(request, 'into') or 0).first(), row)
        elif action == 'delete':
            if empty_series([row.pk]).exists():
                row.delete()
                messages.success(request, f'{row.name} deleted.')
            else:
                messages.error(request, f'{row.name} still has products.')
        url = reverse('catalog_lines')
        return redirect(f'{url}?brand={chosen.id}' if chosen else url)

    rows = ProductSeries.objects.select_related('brand').annotate(
        live_count=Count('products', filter=Q(products__archived_at__isnull=True))).order_by('brand__name', 'name')
    if chosen:
        rows = rows.filter(brand=chosen)
    lines = list(rows)
    by_brand = {}
    for line in lines:
        by_brand.setdefault(line.brand_id, []).append(line)
    for line in lines:
        line.siblings = [other for other in by_brand[line.brand_id] if other.pk != line.pk]
    return render(request, 'stock/catalog_lines.html', {
        'tab': 'lines', 'lines': lines, 'chosen': chosen, 'brands': Brand.objects.order_by('name'),
    })


def _chosen_brand(request):
    value = request.GET.get('brand') or ''
    return Brand.objects.filter(pk=int(value)).first() if value.isdigit() else None

