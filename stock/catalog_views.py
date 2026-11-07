"""Settings -> Catalog: the lists behind every product, for admins."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .models import (Brand, Category, Concentration, FragranceFamily, Product, ProductSeries, ProductType)
from .models.catalog import PERFUME
from .permissions import admin_required, manager_required
from .services.brand_logo import clean_logo
from .services.catalog_admin import (NameTaken, delete_type, merge_brands, merge_lines, new_type_code, rename_brand,
                                     rename_line)
from .services.catalog_prune import empty_brands, empty_series
from .services.names import title_case
from .services.product_contents import parse_pasted
from .services.portal import catalog_access
from .services.restock_requests import demand_rows, request_restock

SWITCHES = ('takes_concentration', 'takes_volume', 'wholesale_discount', 'is_gift', 'shows_scent_details',
            'has_contents', 'picks_perfume')


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


def _save_logo(request, row):
    upload = request.FILES.get('logo')
    if upload is None:
        messages.error(request, 'Choose a picture.')
        return
    try:
        cleaned = clean_logo(upload)
    except ValueError as error:
        messages.error(request, str(error))
        return
    if row.logo:
        row.logo.delete(save=False)
    row.logo.save(f'{slugify(row.name) or row.pk}.png', cleaned, save=True)
    messages.success(request, f'{row.name}: logo saved.')


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
        elif action == 'logo':
            _save_logo(request, row)
        elif action == 'remove_logo':
            if row.logo:
                row.logo.delete(save=True)
            messages.success(request, f'{row.name}: logo removed.')
        elif action == 'delete':
            if empty_brands([row.pk], keep_logos=False).exists():
                if row.logo:
                    row.logo.delete(save=False)
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


def _breaks_the_perfume_rules(old, new):
    """Auto pricing wants a category called exactly Perfumes, the wholesale discount
    and Shopify one with "perfum" in its name (services/pricing, wholesale_discount)."""
    old, new = old.strip().lower(), new.strip().lower()
    return (old == 'perfumes' and new != old) or ('perfum' in old and 'perfum' not in new)


def _category_block(row):
    """Why a category cannot go, or '' if it can."""
    if Product.objects.filter(category=row).exists():
        return f'{row.name} still has products.'
    if Category.objects.filter(parent=row).exists():
        return f'{row.name} has subcategories.'
    kind = row.product_types.first()
    return f'{row.name} is the category of {kind.name}.' if kind else ''


@login_required
@admin_required
def catalog_categories(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        row = Category.objects.filter(pk=_number(request, 'id') or 0).first()
        name = _text(request, 'name', 50)
        taken = Category.objects.filter(name__iexact=name).exclude(pk=row.pk if row else None).exists()
        if action in ('add', 'save') and not name:
            messages.error(request, 'Give the category a name.')
        elif action in ('add', 'save') and taken:
            messages.error(request, f'{name} already exists.')
        elif action == 'add':
            Category.objects.create(name=name, form_kind=PERFUME, sync_to_shopify=request.POST.get('sync_to_shopify') == 'on')
            messages.success(request, f'{name} added.')
        elif row is None:
            messages.error(request, 'That category no longer exists.')
        elif action == 'save' and _breaks_the_perfume_rules(row.name, name):
            messages.error(request, f'{row.name} keeps its name: auto pricing and the wholesale discount read it.')
        elif action == 'save':
            row.name, row.sync_to_shopify = name, request.POST.get('sync_to_shopify') == 'on'
            row.save(update_fields=['name', 'sync_to_shopify'])
            messages.success(request, f'{name} saved.')
        elif action == 'delete':
            block = _category_block(row)
            if block:
                messages.error(request, block)
            else:
                row.delete()
                messages.success(request, f'{row.name} deleted.')
        return redirect('catalog_categories')

    categories = list(Category.objects.select_related('parent').annotate(
        live_count=Count('product', filter=Q(product__archived_at__isnull=True))).order_by('name'))
    return render(request, 'stock/catalog_categories.html', {'tab': 'categories', 'categories': categories})


def _simple_list(request, model, fields, url_name):
    """Add, save and delete for a list of names with a few text fields and an order."""
    action = request.POST.get('action')
    row = model.objects.filter(pk=_number(request, 'id') or 0).first()
    name = _text(request, 'name', 60)
    taken = model.objects.filter(name__iexact=name).exclude(pk=row.pk if row else None).exists()
    if action in ('add', 'save') and not name:
        messages.error(request, 'Give it a name.')
    elif action in ('add', 'save') and taken:
        messages.error(request, f'{name} already exists.')
    elif action == 'add' or (action == 'save' and row is not None):
        row = row if action == 'save' else model()
        row.name = name
        for field, limit in fields:
            setattr(row, field, _text(request, field, limit))
        order = _number(request, 'sort_order')
        if order is not None:
            row.sort_order = order
        row.save()
        messages.success(request, f'{name} saved.')
    elif row is None:
        messages.error(request, 'That row no longer exists.')
    elif action == 'delete':
        count = row.products.count()
        if count:
            messages.error(request, f'{row.name} stays: {count} product{" uses" if count == 1 else "s use"} it.')
        else:
            row.delete()
            messages.success(request, f'{row.name} deleted.')
    return redirect(url_name)


@login_required
@admin_required
def catalog_strengths(request):
    if request.method == 'POST':
        return _simple_list(request, Concentration, (('short', 12), ('shopify_tag', 60)), 'catalog_strengths')
    rows = Concentration.objects.annotate(
        live_count=Count('products', filter=Q(products__archived_at__isnull=True)))
    return render(request, 'stock/catalog_strengths.html', {'tab': 'strengths', 'rows': rows})


@login_required
@admin_required
def catalog_families(request):
    if request.method == 'POST':
        return _simple_list(request, FragranceFamily, (('shopify_tag', 60),), 'catalog_families')
    rows = FragranceFamily.objects.annotate(
        live_count=Count('products', filter=Q(products__archived_at__isnull=True)))
    return render(request, 'stock/catalog_families.html', {'tab': 'families', 'rows': rows})


@login_required
@manager_required
@require_POST
def parse_contents(request):
    """Names pasted into the list on the product form."""
    return JsonResponse({'rows': parse_pasted(_text(request, 'brand', 80), request.POST.get('text') or '')})


@catalog_access
@require_POST
def restock_request(request, pk):
    """The sold-out row's button on the catalogue."""
    from .services.portal import account_of
    from .views import _back_to
    from .wholesale_views import _customer
    try:
        request_restock(get_object_or_404(Product, pk=pk), request.user, _customer(request))
    except ValueError as error:
        messages.info(request, str(error))
    return _back_to(request, 'catalog')


@login_required
@manager_required
def restock_requests(request):
    from .services.restock_requests import arrived_rows
    return render(request, 'stock/restock_requests.html', {'rows': demand_rows(), 'arrived': arrived_rows()})


@login_required
@manager_required
@require_POST
def restock_notify(request, pk):
    """Open WhatsApp with the message ready, and remember the customer was told."""
    from django.utils import timezone
    from urllib.parse import quote
    from .models import RestockRequest
    from .services.contacts import whatsapp_url
    from .services.restock_requests import tell_message
    ask = get_object_or_404(RestockRequest.objects.select_related('product', 'customer'), pk=pk)
    customer = ask.customer or getattr(getattr(ask.requested_by, 'customer_account', None), 'customer', None)
    url = whatsapp_url(customer.phone if customer else '')
    if not url:
        messages.error(request, 'No WhatsApp number for this customer.')
        return redirect('restock_requests')
    RestockRequest.objects.filter(pk=ask.pk).update(notified_at=timezone.now())
    text = tell_message(ask, request.build_absolute_uri(reverse('catalog')))
    return redirect(f'{url}?text={quote(text)}')

