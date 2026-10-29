# stock/views.py
import csv
from decimal import Decimal, InvalidOperation,ROUND_HALF_UP
import hashlib
import json
import re
from io import BytesIO
from collections import defaultdict
from datetime import timedelta, datetime, date
import qrcode
import base64
from calendar import monthrange, Calendar
from urllib.parse import quote_plus
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Count, Max, Case, When
from django.db.models.functions import Coalesce
from django.http import (
    HttpResponse, JsonResponse, FileResponse
)
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.text import slugify
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST
from django.urls import reverse
from urllib.parse import urlencode

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak,KeepTogether,HRFlowable

from django.db.models import Sum, OuterRef, Subquery, IntegerField, F, DecimalField, DateTimeField, ExpressionWrapper, Q,Value
from io import BytesIO as _BytesIO

from .forms import (
    ProductForm, CustomerForm, ARInvoiceForm, ARPaymentForm,
    SaleOrderCorrectionForm, SupplierForm, EmployeeAccountForm,
    PrintProfileForm, InboundOrderEditForm, InboundPurchaseFormSet, DirectPurchaseEditForm,
    InboundReceiveForm, InboundPendingFormSet, StoreForm,
)
from .models.catalog import FORM_KINDS, GENERAL, PERFUME
from .models import (
    Product, Purchase, Sale, Supplier, Customer, ProductImage,
    Category, SaleOrder, InboundOrder, InboundPendingItem, ARInvoice, ARItem,
    Brand, SaleOrderChangeLog, AttendanceRecord, PrintProfile,
    StockAdjustmentLog, SaleOrderPayment, Store, StoreProfile, search_devices,
    PedidoDraft,
)
from .services import order_charges
from .services import submissions
from .services import card_fees
from .permissions import (
    has_admin_access,
    has_manager_access,
    has_order_reconciliation_access,
    has_sales_sensitive_access,
    admin_required,
    manager_required,
)
from .stores import (
    ACTIVE_STORE_SESSION_KEY,
    ALL_STORES,
    available_stores,
    can_switch_store,
    resolve_active_store,
    scope_products_by_store,
    scope_sales_by_store,
    store_for_new_sale,
)
from .services.cloudinary_urls import product_image_cdn_url
from .services.dashboard import (
    build_monthly_dashboard_snapshot,
    build_period_comparison,
    build_target_progress,
    build_yearly_sales_overview,
    order_tender_amounts,
    resolve_dashboard_month,
    resolve_year,
)
from .services.inventory import build_inventory_snapshot
from .services.low_stock import low_stock_data
from .services.order_corrections import (
    delete_sale_order_correction,
    save_sale_order_correction,
)
from .services.profit import sale_profit_map_for_sale_ids
from .services.stock_ops import consume_stock_fifo
from .services.stock_ledger import build_stock_ledger
from .services import rebuild_all_daily_summaries

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import PatternFill, Alignment, Font, Border, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage


def build_product_label(product):
    if not product:
        return 'Unknown product'
    return product.display_name


def get_product_image_url(product):
    if not product:
        return ''

    images_manager = getattr(product, 'images', None)
    if images_manager is None:
        return ''

    for image in images_manager.all():
        image_file = getattr(image, 'image', None)
        if not image_file:
            continue
        try:
            return image_file.url
        except ValueError:
            continue
    return ''


def build_brand_series_map():
    mapping = {}
    for brand in Brand.objects.order_by('name').prefetch_related('series'):
        mapping[str(brand.id)] = [
            {'id': series.id, 'name': series.name}
            for series in brand.series.all().order_by('name')
        ]
    return mapping


def build_brand_catalog():
    catalog = []
    for brand in Brand.objects.order_by('name').prefetch_related('categories'):
        catalog.append({
            'id': brand.id,
            'name': brand.name,
            'category_ids': [str(category.id) for category in brand.categories.all().order_by('name')],
        })
    return catalog


def annotate_catalog_metrics(queryset):
    stock_subquery = (
        Purchase.objects
        .filter(product=OuterRef('pk'))
        .values('product')
        .annotate(total=Sum('remaining'))
        .values('total')[:1]
    )
    sold_subquery = (
        Sale.objects
        .filter(product=OuterRef('pk'))
        .values('product')
        .annotate(total=Sum('quantity'))
        .values('total')[:1]
    )

    return queryset.annotate(
        image_count=Count('images', distinct=True),
        total_stock=Coalesce(Subquery(stock_subquery, output_field=IntegerField()), Value(0)),
        total_sold=Coalesce(Subquery(sold_subquery, output_field=IntegerField()), Value(0)),
    )


def customer_catalog_case(value):
    """Title case for the customer list, keeping the perfume tokens upright.

    Plain ``.title()`` turns "100ml" into "100Ml" and "EDP" into "Edp", which
    is how a 20ml bottle came to print as "20Ml" beside the real thing.
    """
    value = (value or '').strip()
    if not value:
        return ''
    value = value.title()
    value = re.sub(r'\b(Edp|Edt|Edc)\b', lambda m: m.group(1).upper(), value)
    return re.sub(r'(\d)\s*Ml\b', r'\1ml', value)


def build_customer_product_title(product):
    model_part = (getattr(product, 'model', '') or '').strip()
    name_part = (getattr(product, 'name', '') or '').strip()
    title_parts = []
    if model_part:
        title_parts.append(model_part)
    if name_part and name_part.lower() != model_part.lower():
        title_parts.append(name_part)
    return customer_catalog_case(' - '.join(title_parts) or name_part or build_product_label(product))


# The last one on the shelf is the display sample, not something to sell, so
# it never counts towards what a customer can have. A list that offers the
# sample either loses the display or disappoints whoever ordered it.
SAMPLE_RESERVE = 1

# Fewer than three sellable is "low stock": at one or two, a customer ordering
# a couple can empty the shelf, so it is worth saying so rather than promising
# availability we may not have by the time they arrive.
LOW_STOCK_BELOW = 3


def product_ids_on_order():
    """Products sitting on an inbound order that has not landed yet.

    Nothing new is recorded for this - an order placed with a supplier and not
    yet received *is* the definition of in transit, and the app already tracks
    it. So "in stock soon" stays true on its own, with nobody maintaining a
    second flag that would drift out of date.
    """
    return set(InboundPendingItem.objects
               .filter(inbound_order__status='pending_receipt')
               .values_list('product_id', flat=True))


# fill, font - light enough to read black text over, and distinct in greyscale
AVAILABILITY_STYLES = {
    'in-stock': ('FFDCFCE7', 'FF166534'),     # light green
    'low-stock': ('FFFEF3C7', 'FF92400E'),    # light yellow
    'incoming': ('FFDBEAFE', 'FF1E40AF'),     # light blue - on order
    'out-stock': ('FFFEE2E2', 'FF991B1B'),    # red
}


# The customer list is written in European Portuguese: it goes to customers in
# Portugal, and a list half in English reads as somebody else's paperwork.
PT_AVAILABILITY = {
    'in-stock': 'Disponível',
    'low-stock': 'Stock reduzido',
    'incoming': 'Brevemente em stock',
    'out-stock': 'Indisponível',
}

# PVP is the recommended retail price shown to customers, which the shop sets
# above its own till price. Held here rather than on each product so it is one
# number to change, not several hundred.
PVP_UPLIFT = Decimal('2.00')


def pvp_price(retail):
    """The price a customer is quoted: the till price plus the PVP uplift."""
    if retail is None:
        return None
    return (Decimal(retail) + PVP_UPLIFT).quantize(Decimal('0.01'))


# A wholesale discount is margin off a normal bottle of perfume. A 50ml is
# already cheap, so the same few euros off would take most of its margin; a
# 200ml is a body spray, priced by the litre rather than by the fragrance.
# Both ends of the range matter - this is not "big bottles", it is "the size a
# bottle of perfume comes in".
DISCOUNT_MIN_VOLUME_ML = 60
DISCOUNT_MAX_VOLUME_ML = 120


def discount_applies(product):
    """Whether an export discount may come off this product's wholesale price.

    Normal bottles of perfume only - 60ml to 120ml. A product with no volume
    recorded is left out on purpose: quoting a discount we did not mean costs
    the shop margin quietly, while missing one is visible and easily
    corrected.
    """
    category = getattr(product.category, 'name', '') if product.category_id else ''
    if 'perfum' not in (category or '').lower():
        return False
    volume = getattr(product, 'volume_ml', None)
    if volume is None or not (DISCOUNT_MIN_VOLUME_ML <= volume <= DISCOUNT_MAX_VOLUME_ML):
        return False
    # Size already excludes the 200ml sprays; this catches a body mist that
    # happens to be bottled at 100ml.
    strength = getattr(getattr(product, 'concentration', None), 'name', '') or ''
    return 'mist' not in strength.lower()


def discounted_wholesale(wholesale, discount):
    """Wholesale less whatever discount this export is being sent with.

    Never negative: a list quoting a price below zero would be worse than
    quoting the full one.
    """
    if wholesale is None:
        return None
    value = Decimal(wholesale) - (discount or Decimal('0'))
    return max(value, Decimal('0')).quantize(Decimal('0.01'))


def sellable_stock(stock_val):
    """What a customer can actually be sold, with the display sample held back."""
    return max(int(stock_val or 0) - SAMPLE_RESERVE, 0)




def get_catalog_availability_parts(stock_val, on_order=False):
    sellable = sellable_stock(stock_val)
    if sellable >= LOW_STOCK_BELOW:
        return 'Available now', 'in-stock'
    if sellable > 0:
        # Still the honest answer even when more is coming: what matters to
        # someone buying today is how many are on the shelf today.
        return 'Low stock', 'low-stock'
    if on_order:
        return 'In stock soon', 'incoming'
    return 'Currently unavailable', 'out-stock'


SHOPIFY_PRODUCT_CSV_HEADERS = [
    'Title',
    'URL handle',
    'Description',
    'Vendor',
    'Product category',
    'Type',
    'Tags',
    'Published on online store',
    'Status',
    'SKU',
    'Barcode',
    'Option1 name',
    'Option1 value',
    'Option1 Linked To',
    'Option2 name',
    'Option2 value',
    'Option2 Linked To',
    'Option3 name',
    'Option3 value',
    'Option3 Linked To',
    'Price',
    'Compare-at price',
    'Cost per item',
    'Charge tax',
    'Tax code',
    'Unit price total measure',
    'Unit price total measure unit',
    'Unit price base measure',
    'Unit price base measure unit',
    'Inventory tracker',
    'Inventory quantity',
    'Continue selling when out of stock',
    'Weight value (grams)',
    'Weight unit for display',
    'Requires shipping',
    'Fulfillment service',
    'Product image URL',
    'Image position',
    'Image alt text',
    'Variant image URL',
    'Gift card',
    'SEO title',
    'SEO description',
    'Color (product.metafields.shopify.color-pattern)',
    'Google Shopping / Google product category',
    'Google Shopping / Gender',
    'Google Shopping / Age group',
    'Google Shopping / Manufacturer part number (MPN)',
    'Google Shopping / Ad group name',
    'Google Shopping / Ads labels',
    'Google Shopping / Condition',
    'Google Shopping / Custom product',
    'Google Shopping / Custom label 0',
    'Google Shopping / Custom label 1',
    'Google Shopping / Custom label 2',
    'Google Shopping / Custom label 3',
    'Google Shopping / Custom label 4',
]

# The CSV "Product category" column feeds Shopify's *Standard Product Taxonomy*, which
# expects a valid taxonomy path — NOT our local category name. A bare name like
# "Perfumes" is unmatched, so Shopify silently drops it and ML-auto-categorizes the
# product (which mis-filed everything under a Pet node). Map our category → the full
# Shopify taxonomy path so imports land correctly. Unknown categories stay blank.
SHOPIFY_TAXONOMY_BY_CATEGORY = {
    'Perfumes': 'Health & Beauty > Personal Care > Cosmetics > Perfumes & Colognes > Eaux de Parfum',
}
# The Google Shopping column uses Google's *separate* product taxonomy, also unmatched
# by a bare "Perfumes"; map it to Google's perfume node.
GOOGLE_CATEGORY_BY_CATEGORY = {
    'Perfumes': 'Health & Beauty > Personal Care > Cosmetics > Perfume & Cologne',
}


def _clean_csv_text(value):
    return (str(value).strip() if value is not None else '')


def _build_shopify_base_title(product):
    seen = set()
    parts = []
    for value in [product.brand, product.model, product.name]:
        cleaned = _clean_csv_text(value)
        key = cleaned.lower()
        if cleaned and key not in seen:
            parts.append(cleaned)
            seen.add(key)
    return ' - '.join(parts) or product.display_name or product.barcode


def _build_unique_shopify_handle(raw_title, fallback, used_handles):
    base = slugify(raw_title, allow_unicode=False).strip('-')
    if not base:
        base = slugify(fallback, allow_unicode=False).strip('-') or f'product-{fallback}'
    base = (base[:220].strip('-') or f'product-{fallback}')
    handle = base
    suffix = 2
    while handle in used_handles:
        suffix_text = f'-{suffix}'
        handle = f'{base[:255 - len(suffix_text)]}{suffix_text}'
        suffix += 1
    used_handles.add(handle)
    return handle


def _format_shopify_money(value):
    if value is None:
        return ''
    return str(Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _first_clean_product_value(group_products, attr):
    for product in group_products:
        cleaned = _clean_csv_text(getattr(product, attr, ''))
        if cleaned:
            return cleaned
    return ''


def _build_shopify_tags(group_products):
    seen = set()
    tags = []
    for product in group_products:
        values = [
            getattr(product.category, 'name', ''),
            product.brand,
            product.model,
            product.name,
            product.spec,
            product.color,
            product.gender_shopify_tag,
        ]
        for value in values:
            cleaned = _clean_csv_text(value)
            key = cleaned.lower()
            if cleaned and key not in seen:
                tags.append(cleaned)
                seen.add(key)
    return ', '.join(tags)


def _build_shopify_color_metafield(group_products):
    colors = []
    seen = set()
    for product in group_products:
        color = _clean_csv_text(product.color)
        key = color.lower()
        if color and key not in seen:
            colors.append(color)
            seen.add(key)
    return '; '.join(colors)


def _build_shopify_image_url(request, product):
    image_url = get_product_image_url(product)
    if not image_url:
        return ''
    # Shopify fetches this URL from its own servers, so the local absolute URL
    # (a LAN address) is only a fallback for when Cloudinary is not configured.
    return product_image_cdn_url(product) or request.build_absolute_uri(image_url)


def _first_shopify_image_url(request, group_products):
    for product in group_products:
        image_url = _build_shopify_image_url(request, product)
        if image_url:
            return image_url
    return ''


def _apply_shopify_option_columns(row, product, group_products):
    has_spec = any(_clean_csv_text(item.spec) for item in group_products)
    has_color = any(_clean_csv_text(item.color) for item in group_products)
    spec_value = _clean_csv_text(product.spec) or 'Default'
    color_value = _clean_csv_text(product.color) or 'Default'

    if has_spec:
        row['Option1 name'] = 'Spec'
        row['Option1 value'] = spec_value
        if has_color:
            row['Option2 name'] = 'Color'
            row['Option2 value'] = color_value
            row['Option2 Linked To'] = 'product.metafields.shopify.color-pattern'
        return

    if has_color:
        row['Option1 name'] = 'Color'
        row['Option1 value'] = color_value
        row['Option1 Linked To'] = 'product.metafields.shopify.color-pattern'
        return

    if len(group_products) > 1:
        row['Option1 name'] = 'SKU'
        row['Option1 value'] = product.barcode
        return

    row['Option1 name'] = 'Title'
    row['Option1 value'] = 'Default Title'


def get_month_bounds(month_value=None):
    today = timezone.localdate()
    if month_value:
        try:
            year_str, month_str = month_value.split('-', 1)
            year = int(year_str)
            month = int(month_str)
            month_start = today.replace(year=year, month=month, day=1)
        except (TypeError, ValueError):
            month_start = today.replace(day=1)
    else:
        month_start = today.replace(day=1)

    last_day = monthrange(month_start.year, month_start.month)[1]
    month_end = month_start.replace(day=last_day)
    return month_start, month_end


def format_duration_hours(duration):
    if not duration:
        return "0.0h"
    total_seconds = max(duration.total_seconds(), 0)
    hours = total_seconds / 3600
    return f"{hours:.1f}h"


def append_attendance_note(existing_note, new_note, prefix):
    new_note = (new_note or '').strip()
    if not new_note:
        return existing_note or ''
    if existing_note:
        return f"{existing_note}\n{prefix}: {new_note}"
    return f"{prefix}: {new_note}"


def normalize_recorded_at(recorded_at):
    if timezone.is_naive(recorded_at):
        return timezone.make_aware(recorded_at, timezone.get_current_timezone())
    return timezone.localtime(recorded_at)


def comparable_recorded_at(recorded_at):
    if recorded_at is None:
        return None
    return normalize_recorded_at(recorded_at)


def group_purchases_by_recorded_second(purchases):
    grouped = defaultdict(list)
    for purchase in purchases:
        group_key = comparable_recorded_at(purchase.date).replace(microsecond=0)
        grouped[group_key].append(purchase)
    return grouped


def annotate_inbound_formset_runtime(formset):
    for line_form in formset.forms:
        purchase = getattr(line_form, 'instance', None)
        if not purchase or not getattr(purchase, 'pk', None):
            continue
        purchase.sold_units = max((purchase.quantity or 0) - (purchase.remaining or 0), 0)
        purchase.line_total = (purchase.cost_price or Decimal('0.00')) * (purchase.quantity or 0)


# -----------------------------
# 入库
# -----------------------------
def _pending_inbound_orders():
    return (
        InboundOrder.objects
        .filter(status='pending_receipt')
        .select_related('supplier')
        .prefetch_related('pending_items__product__images')
        .order_by('-created_at')
    )


def _build_pending_reviews():
    """For each pending order build the editable review form + formset rendered
    inside a per-order modal on the inbound page."""
    reviews = []
    for order in _pending_inbound_orders():
        formset = InboundPendingFormSet(instance=order, prefix='lines')
        lines = []
        for line_form in formset.forms:
            item = line_form.instance
            item.image_url = get_product_image_url(item.product) if item.pk else ''
            item.display_name = build_product_label(item.product) if item.pk else ''
            lines.append({'form': line_form, 'item': item})
        reviews.append({
            'order': order,
            'form': InboundReceiveForm(instance=order),
            'formset': formset,
            'lines': lines,
        })
    return reviews


def _check_lines(order):
    """What staff need to check a delivery against: photo, name, EAN, quantity.
    No supplier, invoice or cost - the check list is open to every account."""
    lines = []
    for item in order.pending_items.all():
        lines.append({
            'image_url': get_product_image_url(item.product),
            'name': build_product_label(item.product),
            'ean': item.product.barcode or '',
            'quantity': item.quantity,
        })
    return lines


@login_required
def inbound_checklists_view(request):
    """Deliveries on the way, for staff to check off when the boxes arrive."""
    orders = []
    for order in _pending_inbound_orders():
        items = list(order.pending_items.all())
        orders.append({
            'id': order.id,
            'placed': order.placed_at or order.created_at,
            'products': len(items),
            'units': sum(i.quantity for i in items),
        })
    return render(request, 'stock/inbound_checklists.html', {'orders': orders})


@login_required
def inbound_checklist_view(request, order_id):
    order = get_object_or_404(
        InboundOrder.objects.prefetch_related('pending_items__product__images'),
        id=order_id, status='pending_receipt',
    )
    lines = _check_lines(order)
    return render(request, 'stock/inbound_checklist.html', {
        'order_id': order.id,
        'placed': order.placed_at or order.created_at,
        'lines': lines,
        'units': sum(l['quantity'] for l in lines),
        'back_url': reverse('inbound') if has_manager_access(request.user) else reverse('inbound_checklists'),
    })


def _back_to(request, default, *, avoid=()):
    """Redirect to the page the form came from - ``next``, filled in by the
    Back trail (base.html) with the exact list page, filters and search - or
    to ``default``. ``avoid``: paths that no longer exist after this request
    (the deleted record's own pages)."""
    from urllib.parse import urlsplit
    from django.utils.http import url_has_allowed_host_and_scheme
    nxt = (request.POST.get('next') or '').strip()
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        path = urlsplit(nxt).path
        if not any(path.startswith(a) for a in avoid):
            return redirect(nxt)
    return redirect(default)


def csrf_failure(request, reason=""):
    """A stale form (an old copy restored by a phone browser) was sent.

    Says plainly that nothing was recorded and offers the page again, instead
    of Django's bare "Forbidden". The retry is a GET of the same address, so it
    can never resend the old form.
    """
    from django.utils.http import url_has_allowed_host_and_scheme
    retry = request.path if url_has_allowed_host_and_scheme(request.path, allowed_hosts=None) else '/'
    return render(request, 'stock/csrf_failure.html', {'retry_url': retry}, status=403)


def _bought_again(product):
    """Receiving a product means it is being restocked again: a bell that was
    switched off comes back on, and the product returns to low stock when it
    runs low."""
    if not product.restock:
        Product.objects.filter(pk=product.pk).update(restock=True)
        product.restock = True


def _refreshes_low_stock(view):
    """Inbound orders decide what is "on the way", which keeps a product out of
    low stock - so any change to one drops the cached list."""
    from functools import wraps

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        if request.method == 'POST':
            from .services.low_stock import forget
            forget()
        return response
    return wrapped


@login_required
@manager_required
@_refreshes_low_stock
@never_cache        # a reopened phone browser loads it fresh, not an old copy
def inbound_view(request):

    today = timezone.localdate()
    context = {
        'today': today,
        'pending_reviews': _build_pending_reviews(),
    }

    if request.method != 'POST':
        return render(request, 'stock/inbound.html', context)

    # 前端传过来的字段（多行明细 + 可选发票信息）
    items_json  = (request.POST.get('items_json') or '[]').strip()
    supplier_id = request.POST.get('supplier') or None
    invoice_no  = (request.POST.get('invoice_no') or '').strip()
    invoice_date = (request.POST.get('invoice_date') or '').strip()

    # 解析购物车
    try:
        items = json.loads(items_json)
        assert isinstance(items, list) and items, 'Cart is empty.'
    except Exception:
        context['error'] = 'Cart is empty or invalid.'
        return render(request, 'stock/inbound.html', context)

    supplier = Supplier.objects.filter(id=supplier_id).first() if supplier_id else None

    # The same form posted again (refresh, a reopened browser resending, a
    # double tap) is recognised by its one-time token and not recorded twice.
    if submissions.already_claimed(request):
        messages.info(request, 'That stock-in was already recorded - it was not added again.')
        return redirect('inbound')
    # A page cached before tokens existed: the old short guard still applies.
    if not submissions.token_of(request):
        idem_raw = '|'.join([request.path, items_json, str(supplier_id or ''), invoice_no, invoice_date])
        idem_key = 'idem:' + hashlib.sha256(idem_raw.encode('utf-8', 'ignore')).hexdigest()
        if not cache.add(idem_key, 1, timeout=8):
            messages.info(request, 'That stock-in was already recorded - it was not added again.')
            return redirect('inbound')

    try:
        with transaction.atomic():
            submissions.claim(request)      # rolls back with the stock-in if it fails
            order_total = Decimal('0.00')

            if supplier:
                # 有供应商：创建“暂定（pending_receipt）”入库单，先不入库、不产生库存，
                # 行项目存为 InboundPendingItem，待确认收货后再转为 Purchase。
                order = InboundOrder.objects.create(
                    supplier=supplier,
                    invoice_no=invoice_no or None,
                    invoice_date=invoice_date or timezone.localdate(),
                    status='pending_receipt',
                    # The order is being placed with the supplier now; this is
                    # the clock the lead time is measured from.
                    placed_at=timezone.now(),
                    total_amount=Decimal('0.00'),
                )

                for row in items:
                    barcode = str(row.get('barcode', '')).strip()
                    try:
                        qty = int(row.get('qty', 0))
                        cost_price = Decimal(str(row.get('cost_price', 0)))
                    except Exception:
                        raise ValueError('Quantity/cost must be numbers.')

                    if not barcode or qty < 1 or cost_price <= 0:
                        raise ValueError('Invalid item line.')

                    product = Product.objects.get(barcode=barcode)

                    InboundPendingItem.objects.create(
                        inbound_order=order,
                        product=product,
                        quantity=qty,
                        cost_price=cost_price,
                    )
                    order_total += cost_price * qty

                # 回写整单总额
                order.total_amount = order_total
                order.save(update_fields=['total_amount'])

                context['success'] = (
                    f'Pending order #{order.id} created for {supplier.name}, €{order_total:.2f}. '
                    f'Confirm receipt below when the goods arrive.'
                )
            else:
                # 没有 supplier：普通入库，不创建 InboundOrder
                for row in items:
                    barcode = str(row.get('barcode', '')).strip()
                    try:
                        qty = int(row.get('qty', 0))
                        cost_price = Decimal(str(row.get('cost_price', 0)))
                    except Exception:
                        raise ValueError('Quantity/cost must be numbers.')

                    if not barcode or qty < 1 or cost_price <= 0:
                        raise ValueError('Invalid item line.')

                    product = Product.objects.get(barcode=barcode)

                    Purchase.objects.create(
                        inbound_order=None,     # 关键：不归入入库单
                        product=product,
                        supplier=None,          # 普通入库不记录供应商
                        quantity=qty,
                        remaining=qty,
                        cost_price=cost_price,
                        date=timezone.now(),
                    )
                    _bought_again(product)
                    order_total += cost_price * qty

                context['success'] = (
                    f'Stock added (no supplier). Total €{order_total:.2f}.'
                )

    except submissions.AlreadySubmitted:
        messages.info(request, 'That stock-in was already recorded - it was not added again.')
        return redirect('inbound')
    except Product.DoesNotExist:
        context['error'] = 'One product not found by barcode.'
    except ValueError as e:
        context['error'] = str(e)
    except Exception as e:
        context['error'] = 'Unexpected error: ' + str(e)

    if context.get('success') and not context.get('error'):
        # Redirect, so a refresh shows the page again instead of resending it.
        messages.success(request, context['success'])
        return redirect('inbound')

    # 刷新待收货列表（可能刚新增了一张暂定单）
    context['pending_reviews'] = _build_pending_reviews()
    return render(request, 'stock/inbound.html', context)


@login_required
def suppliers_autocomplete(request):
    q = (request.GET.get('q') or '').strip()
    qs = Supplier.objects.all()
    if q:
        qs = qs.filter(name__icontains=q)
    results = [{'id': s.id, 'name': s.name} for s in qs.order_by('name')[:10]]
    return JsonResponse({'results': results})


@login_required
@manager_required
@_refreshes_low_stock
def inbound_receive_view(request, order_id):
    """复核 / 编辑 / 确认收货 / 取消 一张暂定（pending_receipt）入库单。

    确认收货时，把 InboundPendingItem 行转换为 Purchase（remaining=quantity，
    date=now），并将订单状态置为 received。
    """
    order = get_object_or_404(
        InboundOrder.objects
        .select_related('supplier')
        .prefetch_related('pending_items__product__images'),
        id=order_id,
    )
    # Only pending orders can be reviewed. Previously the status was part of the
    # lookup, so a repeated submit — a double tap, or the browser re-sending the
    # POST — hit an order that the first request had already received and the
    # user got a bare 404 even though the receipt had gone through.
    if order.status != 'pending_receipt':
        messages.info(request, f'Order #{order.id} was already received.')
        return redirect('inbound')

    if request.method == 'POST':
        action = (request.POST.get('action') or 'save').strip()

        if action == 'cancel':
            deleted_id = order.id
            order.delete()
            messages.success(request, f'Pending order #{deleted_id} cancelled.')
            return redirect('inbound')

        form = InboundReceiveForm(request.POST, instance=order)
        formset = InboundPendingFormSet(request.POST, instance=order, prefix='lines')

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                order = form.save()
                formset.save()

                remaining_items = list(order.pending_items.select_related('product').all())
                if not remaining_items:
                    deleted_id = order.id
                    order.delete()
                    messages.success(request, f'Pending order #{deleted_id} cancelled (no products left).')
                    return redirect('inbound')

                order.total_amount = sum(
                    ((it.cost_price or Decimal('0.00')) * it.quantity for it in remaining_items),
                    Decimal('0.00'),
                )
                order.save(update_fields=['total_amount'])

                if action == 'receive':
                    now = timezone.now()
                    for it in remaining_items:
                        Purchase.objects.create(
                            inbound_order=order,
                            product=it.product,
                            supplier=order.supplier,
                            quantity=it.quantity,
                            remaining=it.quantity,
                            cost_price=it.cost_price,
                            date=now,
                        )
                        _bought_again(it.product)
                    order.mark_received(now)
                    order.pending_items.all().delete()
                    messages.success(
                        request,
                        f'Order #{order.id} received. {len(remaining_items)} product(s) added to stock.'
                    )
                    return redirect('inbound')

            messages.success(request, f'Pending order #{order.id} saved.')
            return redirect('inbound_receive', order_id=order.id)

        messages.error(request, 'Please fix the highlighted fields.')

    # 复核/确认在 Inbound 页面的弹窗中完成；GET 或校验失败都回到 Inbound 页。
    return redirect('inbound')


# -----------------------------
# 出库（一次订单多个产品 + 每行可选支付方式）
# 前端通过 items_json 提交：[{barcode, qty, price, payment}, ...]
# 可选 customer_id
# -----------------------------
def _sell_draft(request, raw_id):
    """The open Pedido draft of this store that Sell was asked to open."""
    raw_id = str(raw_id or '').strip()
    if not raw_id.isdigit() or not has_manager_access(request.user):
        return None
    store, is_all = resolve_active_store(request)
    if is_all or store is None:
        return None
    return (PedidoDraft.objects.select_related('customer')
            .filter(id=int(raw_id), store=store, status=PedidoDraft.OPEN).first())


def _sell_line_ctx(product):
    """A product as the Sell cart knows it - the fields check_barcode returns."""
    stock = product.purchase_set.filter(remaining__gt=0).aggregate(s=Sum('remaining'))['s'] or 0
    last_purchase = product.purchase_set.order_by('-date').first()
    image_url = ''
    for image in product.images.all():
        try:
            image_url = image.image.url
        except ValueError:
            image_url = ''
        break
    return {
        'barcode': product.barcode,
        'title': product.display_name,
        'brand': product.brand or '',
        'model': product.model or '',
        'pname': product.name,
        'spec': ' '.join(filter(None, [product.spec, product.color])),
        'stock': int(stock),
        'retail': float(product.default_price) if product.default_price is not None else None,
        'wholesale': float(product.wholesale_price) if product.wholesale_price is not None else None,
        'lastCost': float(last_purchase.cost_price) if last_purchase else None,
        'image_url': image_url,
    }


def _pedido_cart(draft):
    """A Pedido draft as a Sell cart: lines at the quoted prices with the
    quote's discount spread over them, and transport and fees as charges."""
    if draft is None:
        return None
    from .services.pedido_drafts import sell_payload
    ids = [line.get('product_id') for line in draft.lines or []]
    products = {p.id: p for p in Product.objects.filter(id__in=ids).prefetch_related('images')}
    payload = sell_payload(draft, products)
    customer = draft.customer
    return {
        'draft_id': draft.id,
        'customer': ({'id': customer.id, 'label': str(customer)}
                     if customer else None),
        'customer_name': draft.customer_name,
        'lines': [dict(_sell_line_ctx(line['product']), qty=line['quantity'],
                       base=f"{line['unit_price']:.2f}", discount=f"{line['discount']:.2f}")
                  for line in payload['lines']],
        'charges': [{'label': c['label'], 'amount': f"{c['amount']:.2f}"} for c in payload['charges']],
    }


def _sell_error(request, message):
    """Sell's error page; a sale from a Pedido draft comes back with its cart."""
    draft = _sell_draft(request, request.POST.get('pedido_draft_id'))
    return render(request, 'stock/outbound.html', {
        'error': message,
        'pedido_payload': _pedido_cart(draft),
    })


@login_required
@never_cache        # a reopened phone browser loads it fresh, not an old copy
def outbound_view(request):
    # A sale must land on one concrete store. Managers viewing the "All stores"
    # aggregate have no unambiguous target, so block selling until they pick a
    # store (employees are always locked to their home store, never blocked).
    _store, store_is_all = resolve_active_store(request)
    if request.method != 'POST':
        draft = None if store_is_all else _sell_draft(request, request.GET.get('pedido'))
        return render(request, 'stock/outbound.html', {
            'outbound_blocked': store_is_all,
            'active_store_name': _store.name if _store else '',
            'pedido_payload': _pedido_cart(draft),
        })

    if store_is_all:
        return render(request, 'stock/outbound.html', {
            'outbound_blocked': True,
            'error': 'Select a specific store before selling — you are viewing "All stores". '
                     'Switch to the store you are selling from, then try again.',
        })
    draft = _sell_draft(request, request.POST.get('pedido_draft_id'))

    items_json = request.POST.get('items_json', '[]')
    payments_json = request.POST.get('payments_json', '[]')
    customer_id = request.POST.get('customer_id') or None
    customer = Customer.objects.filter(id=customer_id).first() if customer_id else None

    # The same form posted again (refresh, a reopened browser resending, a
    # double tap) is recognised by its one-time token and not recorded twice.
    if submissions.already_claimed(request):
        messages.info(request, 'That sale was already recorded - it was not added again.')
        return redirect('outbound')
    # A page cached before tokens existed: the old short guard still applies.
    if not submissions.token_of(request):
        idem_raw = '|'.join([request.path, items_json, payments_json,
                             request.POST.get('charges_json') or '', str(customer_id or '')])
        idem_key = 'idem:' + hashlib.sha256(idem_raw.encode('utf-8', 'ignore')).hexdigest()
        if not cache.add(idem_key, 1, timeout=8):
            messages.info(request, 'That sale was already recorded - it was not added again.')
            return redirect('outbound')

    try:
        items = json.loads(items_json)
        assert isinstance(items, list) and len(items) > 0
    except Exception:
        return _sell_error(request, 'Cart is empty or invalid.')

    valid_methods = {'cash', 'card', 'mbway'}
    method_priority = {'cash': 0, 'card': 1, 'mbway': 2}

    # 先解析行项目并计算订单总额（price 已是折后单价）
    parsed_items = []
    order_revenue = Decimal('0.00')
    order_qty = 0
    line_payment_totals = defaultdict(Decimal)  # 旧格式（按行支付）回退用
    try:
        for row in items:
            barcode = str(row.get('barcode', '')).strip()
            try:
                qty = int(row.get('qty', 0))
                price = Decimal(str(row.get('price', 0)))
            except Exception:
                raise ValueError('Quantity/price must be numbers.')
            if not barcode or qty < 1 or price <= 0:
                raise ValueError('Invalid product line.')
            line_payment = str(row.get('payment', '')).strip()  # 旧格式可选
            parsed_items.append({'barcode': barcode, 'qty': qty, 'price': price, 'payment': line_payment})
            line_total = price * qty
            order_revenue += line_total
            order_qty += qty
            if line_payment in valid_methods:
                line_payment_totals[line_payment] += line_total
    except ValueError as e:
        return _sell_error(request, str(e))

    # 解析订单级支付拆分（新格式）；缺省时回退到按行支付聚合（旧格式）
    try:
        raw_payments = json.loads(payments_json) if payments_json else []
    except Exception:
        raw_payments = []

    payment_totals = defaultdict(Decimal)
    if isinstance(raw_payments, list) and raw_payments:
        for entry in raw_payments:
            method = str(entry.get('method', '')).strip()
            try:
                amount = Decimal(str(entry.get('amount', 0)))
            except Exception:
                return _sell_error(request, 'Payment amount must be a number.')
            if method not in valid_methods or amount <= 0:
                continue
            payment_totals[method] += amount
    elif line_payment_totals:
        payment_totals = line_payment_totals  # 旧格式回退

    if not payment_totals:
        return _sell_error(request, 'Please choose at least one payment method.')

    # Shipping and fees are paid with the goods but are not sales lines.
    try:
        charges = order_charges.parse_charges(request.POST.get('charges_json'))
    except ValueError as e:
        return _sell_error(request, str(e))
    charge_sum = order_charges.charges_total(charges)

    paid_total = sum(payment_totals.values(), Decimal('0.00'))
    due = order_revenue + charge_sum
    if abs(paid_total - due) > Decimal('0.01'):
        return _sell_error(request, f'Payments (€{paid_total:.2f}) must add up to the total (€{due:.2f}).')

    # 主支付方式：金额最大者（并列时按 cash→card→mbway）
    primary_method = min(
        payment_totals.items(),
        key=lambda kv: (-kv[1], method_priority.get(kv[0], 9)),
    )[0]

    active_store = store_for_new_sale(request)
    try:
        with transaction.atomic():
            submissions.claim(request)      # rolls back with the sale if it fails
            order = SaleOrder.objects.create(customer=customer, store=active_store)

            for row in parsed_items:
                try:
                    product = Product.objects.get(barcode=row['barcode'])
                except Product.DoesNotExist:
                    raise ValueError(f"Product not found by barcode: {row['barcode']}")

                # FIFO 扣减（原子条件更新；select_for_update 在 SQLite 上是 no-op，见 stock_ops）
                # 返回本次消耗的真实成本，作为这笔销售的成本基准存下来。
                line_cost = consume_stock_fifo(product, row['qty'])

                # 行支付方式：旧格式用行内值，新格式（订单级拆分）用主支付方式
                line_method = row['payment'] if row['payment'] in valid_methods else primary_method
                Sale.objects.create(
                    order=order,
                    product=product,
                    customer=customer,
                    store=active_store,
                    quantity=row['qty'],
                    unit_price=row['price'],
                    cost_basis=line_cost,
                    payment_method=line_method,
                    date=timezone.now(),
                )

            # 订单级支付拆分（权威记录）
            for method, amount in payment_totals.items():
                SaleOrderPayment.objects.create(order=order, method=method, amount=amount)
            order_charges.save_charges(order, charges)
            # The card terminal's cut, at today's rate, kept with the order.
            card_fees.record(order, payment_totals)

            # The quote is now a sale; it leaves the Pedido drafts list. The
            # status is checked in the update itself, so the same draft sold
            # twice at once rolls the second sale back instead of doubling it.
            if draft is not None:
                sold = PedidoDraft.objects.filter(id=draft.id, status=PedidoDraft.OPEN).update(
                    status=PedidoDraft.SOLD, sale_order=order, updated_at=timezone.now())
                if not sold:
                    raise ValueError(f'Pedido draft #{draft.id} was already sold.')

    except submissions.AlreadySubmitted:
        messages.info(request, 'That sale was already recorded - it was not added again.')
        return redirect('outbound')
    except ValidationError as e:
        return _sell_error(request, '; '.join(e.messages))
    except ValueError as e:
        return _sell_error(request, str(e))
    except Exception as e:
        return _sell_error(request, 'Unexpected error: ' + str(e))

    pay_summary = ', '.join(
        f'{label} €{payment_totals[code]:.2f}'
        for code, label in (('cash', 'Cash'), ('card', 'Card'), ('mbway', 'MBWay'))
        if payment_totals.get(code)
    )
    charge_note = f' + €{charge_sum:.2f} shipping/fees' if charge_sum else ''
    draft_note = f' Pedido draft #{draft.id} marked sold.' if draft is not None else ''
    # Redirect, so a refresh shows the page again instead of resending the sale.
    messages.success(request, f'Sale completed. {order_qty} item(s), €{order_revenue:.2f}{charge_note} ({pay_summary}).{draft_note}')
    return redirect('outbound')


# -----------------------------
# 产品列表
# -----------------------------
@login_required
def product_list_view(request):
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    query = request.GET.get('q', '').strip()
    selected_category = request.GET.get('category')
    selected_brand = request.GET.get('brand')
    stock_status = (request.GET.get('stock_status') or '').strip()
    sort_by = (request.GET.get('sort') or 'latest').strip()
    active_store, _is_all = resolve_active_store(request)
    state = get_filtered_product_list_state(
        show_sales_sensitive=show_sales_sensitive,
        query=query,
        selected_category=selected_category,
        selected_brand=selected_brand,
        stock_status=stock_status,
        sort_by=sort_by,
        store=active_store,
    )

    products_qs = state['products_qs']
    categories = state['categories']
    brand_options = state['brand_options']
    selected_category_int = state['selected_category']
    selected_brand_id = state['selected_brand']
    sort_by = state['sort_by']
    active_filter_labels = state['active_filter_labels']

    total_types = products_qs.count()
    in_stock_types = products_qs.filter(total_stock__gt=0).count()

    paginator = Paginator(products_qs, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    from .services.low_stock import in_transit_quantities
    transit = in_transit_quantities(p.id for p in page_obj)
    for p in page_obj:
        p.display_price = p.default_price
        p.primary_image_url = get_product_image_url(p)
        p.in_transit = transit.get(p.id, 0)

    total_stock = sum(p.total_stock for p in page_obj)
    query_params = request.GET.copy()
    query_params.pop('page', None)
    context = {
        'products': page_obj,
        'page_obj': page_obj,
        'query': query,
        'categories': categories,
        'brands': brand_options,
        'selected_category': selected_category_int,
        'selected_brand': selected_brand_id,
        'stock_status': stock_status,
        'sort_by': sort_by,
        'total_types': total_types,
        'in_stock_types': in_stock_types,
        'total_stock': total_stock,
        'filter_querystring': query_params.urlencode(),
        'active_filter_labels': active_filter_labels,
        'filters_on': bool(selected_brand_id or stock_status) or sort_by != 'latest',
        'can_manage': has_manager_access(request.user),
        'show_sales_sensitive': show_sales_sensitive,
        'export_defaults': {
            'price_mode': 'retail',
            'only_in_stock': stock_status == 'in_stock',
            'include_images': True,
        },
        'shopify_export_defaults': {
            'only_in_stock': stock_status == 'in_stock',
        },
    }
    return render(request, 'stock/product_list.html', context)


def category_with_descendants(category_id):
    """``category_id`` plus every category beneath it.

    The list page filters by the three top-level categories, so picking
    Accessories has to bring back the cases and the cables too, not an empty
    page. Walks the tree rather than assuming one level, and tolerates a
    cycle - Category.parent is a plain FK with nothing preventing one.
    """
    if not category_id or not str(category_id).isdigit():
        return []
    found, frontier = {int(category_id)}, [int(category_id)]
    while frontier:
        children = list(Category.objects
                        .filter(parent_id__in=frontier)
                        .exclude(id__in=found)
                        .values_list('id', flat=True))
        if not children:
            break
        found.update(children)
        frontier = children
    return sorted(found)


def get_filtered_product_list_state(*, show_sales_sensitive, query, selected_category, selected_brand, stock_status, sort_by, store=None):
    recent_sales_start = timezone.now() - timedelta(days=30)
    stock_subquery = (
        Purchase.objects
        .filter(product=OuterRef('pk'))
        .values('product')
        .annotate(total=Sum('remaining'))
        .values('total')[:1]
    )
    total_sales_subquery = (
        Sale.objects
        .filter(product=OuterRef('pk'))
        .values('product')
        .annotate(total=Sum('quantity'))
        .values('total')[:1]
    )
    recent_sales_subquery = (
        Sale.objects
        .filter(product=OuterRef('pk'), date__gte=recent_sales_start)
        .values('product')
        .annotate(total=Sum('quantity'))
        .values('total')[:1]
    )
    last_sold_subquery = (
        Sale.objects
        .filter(product=OuterRef('pk'))
        .order_by('-date')
        .values('date')[:1]
    )

    products_qs = scope_products_by_store(Product.objects, store).annotate(
        total_stock=Coalesce(Subquery(stock_subquery, output_field=IntegerField()), Value(0), output_field=IntegerField()),
    )
    if show_sales_sensitive:
        products_qs = products_qs.annotate(
            total_sold_qty=Coalesce(Subquery(total_sales_subquery, output_field=IntegerField()), Value(0), output_field=IntegerField()),
            recent_sold_qty=Coalesce(
                Subquery(recent_sales_subquery, output_field=IntegerField()),
                Value(0),
                output_field=IntegerField(),
            ),
            last_sold_at=Subquery(last_sold_subquery, output_field=DateTimeField()),
        )
    else:
        products_qs = products_qs.annotate(
            total_sold_qty=Value(0, output_field=IntegerField()),
            recent_sold_qty=Value(0, output_field=IntegerField()),
            last_sold_at=Value(None, output_field=DateTimeField()),
        )
    products_qs = (
        products_qs
        .select_related('category', 'brand_master', 'series_master')
        .prefetch_related('images')
    )

    if query:
        products_qs = products_qs.filter(
            Q(name__icontains=query) |
            Q(barcode__icontains=query) |
            Q(brand__icontains=query) |
            Q(model__icontains=query) |
            Q(spec__icontains=query) |
            Q(color__icontains=query)
        )

    category_ids = category_with_descendants(selected_category)
    if category_ids:
        products_qs = products_qs.filter(category_id__in=category_ids)

    brand_options = (
        Brand.objects
        .filter(products__isnull=False)
        .order_by('name')
        .distinct()
    )
    if category_ids:
        brand_options = brand_options.filter(
            products__category_id__in=category_ids).distinct()

    selected_brand_id = None
    selected_brand_obj = None
    if selected_brand and str(selected_brand).isdigit():
        selected_brand_id = int(selected_brand)
        selected_brand_obj = Brand.objects.filter(id=selected_brand_id).first()
        if selected_brand_obj:
            products_qs = products_qs.filter(
                Q(brand_master_id=selected_brand_id) |
                Q(brand__iexact=selected_brand_obj.name)
            )

    if stock_status == 'in_stock':
        products_qs = products_qs.filter(total_stock__gt=0)
    elif stock_status == 'low_stock':
        products_qs = products_qs.filter(total_stock__gt=0, total_stock__lte=5)
    elif stock_status == 'out_of_stock':
        products_qs = products_qs.filter(total_stock__lte=0)

    allowed_sort_options = {'latest', 'name_asc', 'stock_desc', 'price_desc', 'price_asc'}
    if show_sales_sensitive:
        allowed_sort_options.update({'sales_desc', 'recent_sales_desc'})
    if sort_by not in allowed_sort_options:
        sort_by = 'latest'

    sort_options = {
        'latest': ['-id'],
        'name_asc': ['brand', 'model', 'name', 'id'],
        'stock_desc': ['-total_stock', 'brand', 'model', 'name'],
        'sales_desc': ['-total_sold_qty', '-recent_sold_qty', 'brand', 'model', 'name'],
        'recent_sales_desc': ['-recent_sold_qty', '-total_sold_qty', 'brand', 'model', 'name'],
        'price_desc': ['-default_price', 'brand', 'model', 'name'],
        'price_asc': ['default_price', 'brand', 'model', 'name'],
    }
    # Bell off and sold out: after all the others, whatever the sort. A product
    # with the bell off but stock left keeps its place.
    products_qs = (products_qs
                   .annotate(stepped_back=Case(When(restock=False, total_stock__lte=0, then=Value(1)),
                                               default=Value(0), output_field=IntegerField()))
                   .order_by('stepped_back', *sort_options.get(sort_by, sort_options['latest'])))

    # Only the top-level categories are offered as filter chips: twelve chips
    # is not a filter, it is a wall. Picking one covers everything beneath it.
    categories = Category.objects.filter(parent__isnull=True).order_by('name')

    active_filter_labels = []
    if query:
        active_filter_labels.append({'label': f'Search: {query}'})
    if selected_category and str(selected_category).isdigit():
        category_obj = Category.objects.filter(id=int(selected_category)).first()
        if category_obj:
            active_filter_labels.append({'label': f'Category: {category_obj.name}'})
    if selected_brand_obj:
        active_filter_labels.append({'label': f'Brand: {selected_brand_obj.name}'})
    if stock_status:
        stock_label_map = {
            'in_stock': 'In stock',
            'low_stock': 'Low stock',
            'out_of_stock': 'Out of stock',
        }
        active_filter_labels.append({'label': f'Stock: {stock_label_map.get(stock_status, stock_status)}'})

    sort_label_map = {
        'latest': 'Latest added',
        'name_asc': 'Name A-Z',
        'stock_desc': 'Stock high to low',
        'sales_desc': 'Best selling',
        'recent_sales_desc': 'Best selling 30 days',
        'price_desc': 'Retail price high to low',
        'price_asc': 'Retail price low to high',
    }
    active_filter_labels.append({'label': f'Sort: {sort_label_map.get(sort_by, "Latest added")}'})

    return {
        'products_qs': products_qs,
        'categories': categories,
        'brand_options': brand_options,
        'selected_category': int(selected_category) if selected_category and str(selected_category).isdigit() else None,
        'selected_brand': selected_brand_id,
        'selected_brand_obj': selected_brand_obj,
        'stock_status': stock_status,
        'sort_by': sort_by,
        'active_filter_labels': active_filter_labels,
    }


@login_required
def export_product_list_excel(request):
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    query = request.GET.get('q', '').strip()
    selected_category = request.GET.get('category')
    selected_brand = request.GET.get('brand')
    stock_status = (request.GET.get('stock_status') or '').strip()
    sort_by = (request.GET.get('sort') or 'latest').strip()
    price_mode = (request.GET.get('price_mode') or 'retail').strip()
    only_in_stock = (request.GET.get('only_in_stock') or '').strip().lower() in {'1', 'true', 'on', 'yes'}
    include_images = (request.GET.get('include_images') or '').strip().lower() in {'1', 'true', 'on', 'yes'}
    export_format = 'pdf' if (request.GET.get('format') or '').strip().lower() == 'pdf' else 'xlsx'
    # A per-export discount off wholesale, so one customer can be quoted a
    # better price without the shop's own numbers being touched.
    try:
        discount = Decimal((request.GET.get('discount') or '0').replace(',', '.').strip() or '0')
    except (InvalidOperation, AttributeError):
        discount = Decimal('0')
    discount = max(discount, Decimal('0'))

    if price_mode not in {'retail', 'wholesale', 'both'}:
        price_mode = 'retail'

    state = get_filtered_product_list_state(
        show_sales_sensitive=show_sales_sensitive,
        query=query,
        selected_category=selected_category,
        selected_brand=selected_brand,
        stock_status=stock_status,
        sort_by=sort_by,
    )
    products_qs = state['products_qs']
    on_order_ids = product_ids_on_order()
    if only_in_stock:
        # Above the sample, not above zero: a product with only the display
        # unit left prints as unavailable, so listing it would contradict the
        # very column beside it.
        #
        # Anything already on order stays in, though. It prints as "Brevemente
        # em stock", which is a real answer for a customer - dropping it would
        # mean losing an order over something arriving this week.
        products_qs = products_qs.filter(
            Q(total_stock__gt=SAMPLE_RESERVE) | Q(pk__in=on_order_ids))

    products = list(products_qs)
    for product in products:
        # Series, name, strength, size - from the recorded volume, not the old
        # free-text spec. Perfume spec was defaulted to "100ML" at one point,
        # so a 20ml bottle can still be carrying that and was printing 100ml.
        # The brand heads its own sheet, so it is not repeated in every row.
        product.export_title = customer_catalog_case(
            product.title_parts(include_brand=False)) or build_product_label(product)
        product.export_brand = customer_catalog_case((product.brand or '').strip()) or 'No Brand'
        product.export_model = customer_catalog_case((product.model or '').strip()) or 'Other Selections'
        product.export_category_name = customer_catalog_case(getattr(product.category, 'name', ''))
        _label, product.export_state = get_catalog_availability_parts(
            product.total_stock, product.id in on_order_ids)
        product.export_availability = PT_AVAILABILITY.get(product.export_state, _label)
        # Low stock and more already on the way are two different facts, and a
        # customer wants both: the row says what is on the shelf now, and a
        # second line under it says more is coming.
        product.export_incoming_note = (
            product.export_state == 'low-stock' and product.id in on_order_ids)
        product.export_pvp = pvp_price(product.default_price)
        product.export_wholesale = discounted_wholesale(
            product.wholesale_price,
            discount if discount_applies(product) else Decimal('0'))

    base_params = request.GET.copy()
    for key in ['price_mode', 'only_in_stock', 'include_images']:
        base_params.pop(key, None)
    fallback_url = reverse('product_list')
    if base_params:
        fallback_url = f"{fallback_url}?{base_params.urlencode()}"

    if not products:
        messages.warning(request, 'No products matched the current filters and export options.')
        return redirect(fallback_url)

    brand_groups = {}
    for product in products:
        brand_groups.setdefault(product.export_brand, []).append(product)

    wb = Workbook()
    title_fill = PatternFill('solid', fgColor='FFF6EFE4')
    header_fill = PatternFill('solid', fgColor='FFE8EEF6')
    section_fill = PatternFill('solid', fgColor='FFF7F4EE')
    thin = Side(border_style='thin', color='FFD7DFE8')
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    wrap = Alignment(wrap_text=True, vertical='top')
    center = Alignment(horizontal='center', vertical='center')
    sheet_names = set()

    def unique_sheet_title(raw_title):
        cleaned = (raw_title or 'Products').strip()
        for char in ['\\', '/', '*', '[', ']', ':', '?']:
            cleaned = cleaned.replace(char, ' ')
        cleaned = cleaned[:31] or 'Products'
        candidate = cleaned
        suffix = 2
        while candidate in sheet_names:
            suffix_text = f" {suffix}"
            candidate = f"{cleaned[:31-len(suffix_text)]}{suffix_text}"
            suffix += 1
        sheet_names.add(candidate)
        return candidate

    # What the colours mean, in the customer's language. The export settings
    # that used to sit here - sort order, "Images: yes" - meant nothing to
    # anyone reading the list.
    legend = ('Cores:  verde = disponível  |  '
              f'amarelo = stock reduzido (menos de {LOW_STOCK_BELOW} unidades)  |  '
              'azul = brevemente em stock (encomendado, normalmente 3-7 dias)  |  '
              'vermelho = indisponível')

    # PDF for anything being sent to a customer. The spreadsheet's photos are
    # floating drawings, which WhatsApp's preview and most phone spreadsheet
    # apps simply do not render - see services/catalog_pdf.py. Returned here,
    # before the workbook is built, so a PDF request does no spreadsheet work.
    if export_format == 'pdf':
        from .services.catalog_pdf import build_catalog_pdf
        response = FileResponse(
            build_catalog_pdf(brand_groups, price_mode=price_mode,
                              include_images=include_images, legend=legend),
            as_attachment=True,
            filename=f"Product_List_{timezone.localtime().strftime('%Y%m%d_%H%M')}.pdf")
        response['Content-Type'] = 'application/pdf'
        return response

    # An extra column beside availability, not an extra row beneath it: the
    # product keeps one line, and "a couple left" and "more arriving" are read
    # across rather than down. Added only when something really is on the way,
    # so an empty column never takes width from the rest.
    show_incoming_column = any(getattr(p, 'export_incoming_note', False)
                               for p in products)

    first_sheet = True
    for brand_name, brand_products in brand_groups.items():
        if first_sheet:
            ws = wb.active
            first_sheet = False
        else:
            ws = wb.create_sheet()
        ws.title = unique_sheet_title(brand_name)

        columns = []
        if include_images:
            columns.append(('Foto', 16))
        columns.extend([
            ('Produto', 34),
            ('EAN', 16),
            ('Categoria', 18),
        ])
        if price_mode in {'retail', 'both'}:
            columns.append(('PVP (EUR)', 15))
        if price_mode in {'wholesale', 'both'}:
            columns.append(('Preço grossista (EUR)', 20))
        columns.append(('Disponibilidade', 20))
        if show_incoming_column:
            columns.append(('A chegar', 22))

        for idx, (_, width) in enumerate(columns, start=1):
            ws.column_dimensions[get_column_letter(idx)].width = width

        max_col = len(columns)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
        ws.cell(row=1, column=1, value=f'{brand_name} | Lista de Produtos')
        ws.cell(row=1, column=1).fill = title_fill
        ws.cell(row=1, column=1).font = Font(bold=True, size=15)
        ws.cell(row=1, column=1).alignment = Alignment(vertical='center')

        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)
        ws.cell(row=2, column=1, value=legend)
        ws.cell(row=2, column=1).alignment = wrap
        ws.cell(row=2, column=1).font = Font(color='FF5B6675', size=9)
        ws.row_dimensions[2].height = 26

        current_row = 4
        model_groups = {}
        for product in brand_products:
            model_groups.setdefault(product.export_model, []).append(product)

        for model_name, model_products in model_groups.items():
            ws.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=max_col)
            ws.cell(row=current_row, column=1, value=model_name)
            ws.cell(row=current_row, column=1).fill = section_fill
            ws.cell(row=current_row, column=1).font = Font(bold=True, size=11)
            current_row += 1

            for col_idx, (header, _) in enumerate(columns, start=1):
                cell = ws.cell(row=current_row, column=col_idx, value=header)
                cell.fill = header_fill
                cell.font = Font(bold=True)
                cell.border = border
                cell.alignment = center
            current_row += 1

            for product in model_products:
                row_idx = current_row
                col_idx = 1

                if include_images:
                    ws.cell(row=row_idx, column=col_idx).border = border
                    ws.cell(row=row_idx, column=col_idx).alignment = center
                    try:
                        first_image = product.images.all()[0]
                        pil = PILImage.open(first_image.image.path)
                        pil.thumbnail((120, 120))
                        tmp = _BytesIO()
                        pil.save(tmp, format='PNG')
                        tmp.seek(0)
                        xlimg = XLImage(tmp)
                        ws.add_image(xlimg, f'{get_column_letter(col_idx)}{row_idx}')
                        ws.row_dimensions[row_idx].height = 88
                    except Exception:
                        pass
                    col_idx += 1

                ws.cell(row=row_idx, column=col_idx, value=product.export_title).alignment = wrap
                ws.cell(row=row_idx, column=col_idx).border = border
                col_idx += 1

                # EAN (barcode) — stored as text so long codes keep every digit.
                ean_cell = ws.cell(row=row_idx, column=col_idx, value=(product.barcode or '-'))
                ean_cell.border = border
                ean_cell.alignment = center
                ean_cell.number_format = '@'
                col_idx += 1

                ws.cell(row=row_idx, column=col_idx, value=product.export_category_name or '-').alignment = wrap
                ws.cell(row=row_idx, column=col_idx).border = border
                col_idx += 1

                if price_mode in {'retail', 'both'}:
                    retail_cell = ws.cell(row=row_idx, column=col_idx, value=float(product.export_pvp) if product.export_pvp is not None else None)
                    # Built-in format (numFmtId 4), never a custom one: a
                    # custom format carrying "EUR" is id 164, and a viewer that
                    # cannot resolve it falls back to a built-in - landing on
                    # m/d/yy, which is why customers saw prices as dates. The
                    # currency lives in the column heading instead.
                    retail_cell.number_format = '#,##0.00'
                    retail_cell.border = border
                    col_idx += 1

                if price_mode in {'wholesale', 'both'}:
                    wholesale_cell = ws.cell(row=row_idx, column=col_idx, value=float(product.export_wholesale) if product.export_wholesale is not None else None)
                    wholesale_cell.number_format = '#,##0.00'
                    wholesale_cell.border = border
                    col_idx += 1

                availability_cell = ws.cell(row=row_idx, column=col_idx, value=product.export_availability)
                availability_cell.border = border
                availability_cell.alignment = center
                # Colour and words both, so the list still reads correctly
                # printed in black and white or by someone colour-blind.
                style = AVAILABILITY_STYLES.get(product.export_state)
                if style:
                    availability_cell.fill = PatternFill('solid', fgColor=style[0])
                    availability_cell.font = Font(color=style[1], bold=True)

                if show_incoming_column:
                    col_idx += 1
                    note_cell = ws.cell(row=row_idx, column=col_idx)
                    note_cell.border = border
                    note_cell.alignment = center
                    if product.export_incoming_note:
                        note_cell.value = PT_AVAILABILITY['incoming']
                        incoming_style = AVAILABILITY_STYLES['incoming']
                        note_cell.fill = PatternFill('solid', fgColor=incoming_style[0])
                        note_cell.font = Font(color=incoming_style[1], bold=True)

                current_row += 1

            current_row += 1

        ws.freeze_panes = 'A5'

    ts = timezone.localtime().strftime('%Y%m%d_%H%M')
    export_buffer = _BytesIO()
    wb.save(export_buffer)
    export_buffer.seek(0)
    filename = f'Customer_Product_List_{ts}.xlsx'
    response = FileResponse(export_buffer, as_attachment=True, filename=filename)
    response['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return response


@login_required
@manager_required
def export_shopify_inventory_csv(request):
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    query = request.GET.get('q', '').strip()
    selected_category = request.GET.get('category')
    selected_brand = request.GET.get('brand')
    stock_status = (request.GET.get('stock_status') or '').strip()
    sort_by = (request.GET.get('sort') or 'latest').strip()
    only_in_stock = (request.GET.get('only_in_stock') or '').strip().lower() in {'1', 'true', 'on', 'yes'}

    state = get_filtered_product_list_state(
        show_sales_sensitive=show_sales_sensitive,
        query=query,
        selected_category=selected_category,
        selected_brand=selected_brand,
        stock_status=stock_status,
        sort_by=sort_by,
    )
    products_qs = state['products_qs']
    if only_in_stock:
        products_qs = products_qs.filter(total_stock__gt=0)

    products = list(products_qs)
    base_params = request.GET.copy()
    for key in ['only_in_stock']:
        base_params.pop(key, None)
    fallback_url = reverse('product_list')
    if base_params:
        fallback_url = f"{fallback_url}?{base_params.urlencode()}"

    if not products:
        messages.warning(request, 'No products matched the current filters and Shopify export options.')
        return redirect(fallback_url)

    product_groups = {}
    for product in products:
        key = (
            _clean_csv_text(product.brand).lower(),
            _clean_csv_text(product.model).lower(),
            _clean_csv_text(product.name).lower(),
            product.category_id,
        )
        product_groups.setdefault(key, []).append(product)

    ts = timezone.localtime().strftime('%Y%m%d_%H%M')
    filename = f'Shopify_Product_Inventory_{ts}.csv'
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')

    writer = csv.DictWriter(response, fieldnames=SHOPIFY_PRODUCT_CSV_HEADERS, lineterminator='\n')
    writer.writeheader()

    used_handles = set()
    for group_products in product_groups.values():
        first_product = group_products[0]
        base_title = _build_shopify_base_title(first_product)
        handle = _build_unique_shopify_handle(base_title, first_product.barcode or first_product.id, used_handles)
        product_description = _first_clean_product_value(group_products, 'description')
        product_category = _clean_csv_text(getattr(first_product.category, 'name', ''))
        shopify_taxonomy = SHOPIFY_TAXONOMY_BY_CATEGORY.get(product_category, '')
        google_category = GOOGLE_CATEGORY_BY_CATEGORY.get(product_category, '')
        product_image_url = _first_shopify_image_url(request, group_products)
        color_metafield = _build_shopify_color_metafield(group_products)
        tags = _build_shopify_tags(group_products)

        for index, product in enumerate(group_products):
            current_stock = int(getattr(product, 'total_stock', 0) or 0)
            variant_image_url = _build_shopify_image_url(request, product)
            row = {header: '' for header in SHOPIFY_PRODUCT_CSV_HEADERS}

            row.update({
                'URL handle': handle,
                'SKU': product.barcode,
                'Barcode': product.barcode,
                'Price': _format_shopify_money(product.default_price),
                'Cost per item': _format_shopify_money(product.current_fifo_cost_price()),
                'Charge tax': 'TRUE',
                'Inventory tracker': 'shopify',
                'Inventory quantity': str(current_stock),
                'Continue selling when out of stock': 'DENY',
                'Weight unit for display': 'g',
                'Requires shipping': 'TRUE',
                'Fulfillment service': 'manual',
                'Variant image URL': variant_image_url,
            })

            if index == 0:
                row.update({
                    'Title': base_title,
                    'Description': product_description,
                    'Vendor': _clean_csv_text(first_product.brand),
                    'Product category': shopify_taxonomy,
                    'Type': product_category,
                    'Tags': tags,
                    'Published on online store': 'TRUE',
                    'Status': 'Active',
                    'Product image URL': product_image_url,
                    'Image position': '1' if product_image_url else '',
                    'Image alt text': base_title if product_image_url else '',
                    'Gift card': 'FALSE',
                    'SEO title': base_title,
                    'SEO description': product_description,
                    'Color (product.metafields.shopify.color-pattern)': color_metafield,
                    'Google Shopping / Google product category': google_category,
                    'Google Shopping / Condition': 'New',
                    'Google Shopping / Custom product': 'FALSE',
                })

            _apply_shopify_option_columns(row, product, group_products)

            writer.writerow(row)

    return response


# -----------------------------
# 产品增/改/详情
# -----------------------------
@login_required
@manager_required
@require_POST
def inbound_order_rollback(request, order_id):
    """Undo a receipt confirmed by mistake, from the supplier's order list.

    Manager-only: this removes stock, and the person who mis-tapped Confirm is
    often the person who would tap Undo just as quickly.
    """
    from .services.inbound_rollback import roll_back_receipt

    order = get_object_or_404(InboundOrder, pk=order_id)
    ok, message = roll_back_receipt(order, request.user)
    (messages.success if ok else messages.error)(request, message)

    nxt = request.POST.get('next') or ''
    if nxt.startswith('/'):
        return redirect(nxt)
    if order.supplier_id:
        return redirect('supplier_detail', supplier_id=order.supplier_id)
    return redirect('inbound')


@login_required
@manager_required
@require_POST
def create_category(request):
    """Add a category, or a subcategory, from the add-product page.

    The shop decides what it sells, so new kinds of goods must not need a
    developer. A subcategory inherits its parent's form kind and its Shopify
    setting, which is almost always what is meant - "Tablet cases" under
    Accessories is an accessory, and stays off the storefront.
    """
    name = (request.POST.get('name') or '').strip()
    parent_id = (request.POST.get('parent') or '').strip()
    form_kind = (request.POST.get('form_kind') or '').strip()

    if not name:
        return JsonResponse({'ok': False, 'error': 'Give the category a name.'}, status=400)
    if len(name) > 50:
        return JsonResponse({'ok': False, 'error': 'Name is too long (50 characters).'},
                            status=400)

    parent = None
    if parent_id.isdigit():
        parent = Category.objects.filter(pk=int(parent_id)).first()
        if parent is None:
            return JsonResponse({'ok': False, 'error': 'That parent no longer exists.'},
                                status=400)

    # Two categories with the same name under the same parent are
    # indistinguishable at the till, so reuse rather than duplicate.
    existing = Category.objects.filter(name__iexact=name, parent=parent).first()
    if existing is not None:
        return JsonResponse({'ok': False, 'error': f'"{existing.name}" already exists.',
                             'id': existing.id}, status=409)

    if parent is not None:
        category = Category.objects.create(
            name=name, parent=parent,
            form_kind=parent.effective_form_kind,
            sync_to_shopify=parent.sync_to_shopify)
    else:
        valid = {choice for choice, _label in FORM_KINDS}
        category = Category.objects.create(
            name=name,
            form_kind=form_kind if form_kind in valid else GENERAL,
            sync_to_shopify=(form_kind == PERFUME))

    return JsonResponse({
        'ok': True,
        'id': category.id,
        'name': category.name,
        'kind': category.effective_form_kind,
        'parent': parent.id if parent else None,
    })


def build_category_picker():
    """Top-level categories, each with its subcategories, for the first step.

    Picking a top-level category with subcategories opens a second row; one
    without them is the whole answer. The parent itself stays selectable as
    "Other <parent>" for goods that fit no subcategory.
    """
    picker = []
    parents = (Category.objects
               .filter(parent__isnull=True)
               .prefetch_related('category_set')
               .order_by('name'))
    for parent in parents:
        children = list(parent.category_set.order_by('name'))
        entry = {
            'id': parent.id,
            'name': parent.name,
            'kind': parent.effective_form_kind,
            'children': [{'id': c.id, 'name': c.name, 'kind': c.effective_form_kind}
                         for c in children],
        }
        if children:
            entry['children'].append({
                'id': parent.id,
                'name': f'Other {parent.name.lower()}',
                'kind': parent.effective_form_kind,
            })
        picker.append(entry)
    return picker


def build_category_kind_map():
    """``{category_id: form_kind}`` so the page can reshape without a round trip."""
    return {str(c.id): c.effective_form_kind
            for c in Category.objects.select_related('parent')}


def build_shelf_targets():
    """``{category_id: [{name, url}]}`` for categories tracked on the shelf.

    Cases and glass are never entered as products: the handset is added once
    and then each colour is marked on the shelf. So choosing one of those
    categories on the add-product page offers the grid rather than a form
    nobody should be filling in.
    """
    from .models import ShelfStyle
    targets = {}
    for style in (ShelfStyle.objects
                  .filter(is_active=True, category__isnull=False)
                  .select_related('category')):
        targets.setdefault(str(style.category_id), []).append({
            'name': style.name,
            'url': reverse('shelf_style', args=[style.slug]),
        })
    return targets


@login_required
@manager_required
def add_product_view(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, can_edit_prices=has_manager_access(request.user))
        if form.is_valid():
            product = form.save()
            for img in request.FILES.getlist('images'):
                ProductImage.objects.create(product=product, image=img)
            return redirect('product_detail', pk=product.pk)
    else:
        # Perfumes are all the shop adds now (2026-10-29); the form opens on it.
        perfume = Category.objects.filter(parent__isnull=True, form_kind=PERFUME).order_by('id').first()
        form = ProductForm(initial={'category': perfume.id if perfume else None},
                           can_edit_prices=has_manager_access(request.user))
    return render(request, 'stock/add_product.html', {
        'form': form,
        'category_groups': build_category_picker(),
        'category_kind_json': json.dumps(build_category_kind_map()),
        'shelf_targets_json': json.dumps(build_shelf_targets()),
        'brand_series_map_json': json.dumps(build_brand_series_map()),
        'brand_catalog_json': json.dumps(build_brand_catalog()),
    })


@login_required
def product_detail_view(request, pk):
    product = get_object_or_404(
        Product.all_objects.select_related('category', 'brand_master', 'series_master').prefetch_related('images'),
        pk=pk
    )

    purchases = product.purchase_set.select_related('supplier').order_by('-date')

    # Sales History on this page shows only the last 10 days; the rest lives on the
    # dedicated product sales-history page (linked via full_sales_url).
    sales_base = (
        product.sale_set
        .annotate(business_at=Coalesce('order__created_at', 'date'))
        .select_related('customer', 'order', 'order__store', 'store', 'order__customer')
        .order_by('-business_at', '-id')
    )
    sales_total_count = sales_base.count()
    recent_cutoff = timezone.localdate() - timedelta(days=10)
    sales = list(sales_base.filter(business_at__date__gte=recent_cutoff))
    sales_older_count = sales_total_count - len(sales)
    full_sales_url = f"{reverse('product_sales_history')}?product={product.id}"

    total_stock = sum(p.remaining for p in purchases if p.remaining > 0)
    fifo_purchase = product.purchase_set.filter(remaining__gt=0).order_by('date').first()
    fifo_price = fifo_purchase.cost_price if fifo_purchase else None

    # Per-supplier cost comparison/trend (purchases is ordered -date, so first seen = latest).
    supplier_cost_map = {}
    for p in purchases:
        entry = supplier_cost_map.setdefault(p.supplier_id, {
            'supplier_id': p.supplier_id,
            'name': p.supplier.name if p.supplier else 'No supplier',
            'batches': 0, 'units': 0, 'costs': [],
            'last_cost': None, 'last_date': None,
        })
        entry['batches'] += 1
        entry['units'] += p.quantity
        if p.cost_price is not None:
            entry['costs'].append(p.cost_price)
        if entry['last_date'] is None:
            entry['last_cost'] = p.cost_price
            entry['last_date'] = p.date

    supplier_costs = []
    for entry in supplier_cost_map.values():
        costs = entry.pop('costs')
        entry['min_cost'] = min(costs) if costs else None
        entry['max_cost'] = max(costs) if costs else None
        entry['avg_cost'] = (sum(costs) / len(costs)) if costs else None
        supplier_costs.append(entry)
    supplier_costs.sort(key=lambda e: (e['last_cost'] is None, e['last_cost'] or Decimal('0.00')))

    priced = [e for e in supplier_costs if e['last_cost'] is not None]
    cheapest_supplier_id = min(priced, key=lambda e: e['last_cost'])['supplier_id'] if len(priced) > 1 else None

    is_manager = has_manager_access(request.user)
    # The full reconstructed Stock Ledger lives on the dedicated /sales-history/
    # page (merged with sales detail); the product page just links there.
    is_perfume = bool(product.category and 'perfum' in (product.category.name or '').lower())

    category_name = (product.category.name if product.category else '').lower()
    return render(request, 'stock/product_detail.html', {
        'product': product,
        # Perfume rows and accessory rows are different facts; showing both
        # leaves half the panel reading "-" whichever kind you are looking at.
        'is_perfume': 'perfum' in category_name,
        'attribute_rows': product.attribute_summary(),
        'fitment_label': product.fitment_label,
        'purchases': purchases,
        'sales': sales,
        'sales_older_count': sales_older_count,
        'full_sales_url': full_sales_url,
        'total_stock': total_stock,
        'fifo_price': fifo_price,
        'supplier_costs': supplier_costs,
        'cheapest_supplier_id': cheapest_supplier_id,
        'show_sensitive': is_manager,
        'is_perfume': is_perfume,
        'show_sales_sensitive': has_sales_sensitive_access(request.user),
    })


@login_required
def product_sales_history_view(request):
    """Dedicated, deep sales page for one product: search by name, then show the
    complete sales detail (store / date-time / order / customer / qty / price /
    payment / profit*) plus the full stock ledger and per-store / per-month rollups."""
    if not has_manager_access(request.user):
        return redirect('sales_records')

    q = request.GET.get('q', '').strip()
    product_id = request.GET.get('product', '').strip()
    start_str = request.GET.get('start_date', '').strip()
    end_str = request.GET.get('end_date', '').strip()
    store_id = request.GET.get('store', '').strip()
    show_profit = bool(getattr(request.user, 'is_superuser', False))
    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    context = {
        'q': q,
        'show_profit': show_profit,
        'stores': Store.objects.order_by('name'),
        'selected_store_id': store_id,
        'start_date': start_str,
        'end_date': end_str,
    }

    product = None
    if product_id.isdigit():
        product = (
            Product.objects.filter(pk=int(product_id))
            .select_related('category', 'brand_master').prefetch_related('images').first()
        )

    if not product:
        matches = []
        if q:
            matches = list(
                Product.objects.filter(
                    Q(name__icontains=q) | Q(barcode__icontains=q)
                    | Q(model__icontains=q) | Q(brand__icontains=q)
                ).annotate(sold=Coalesce(Sum('sale__quantity'), 0)).order_by('brand', 'name')[:50]
            )
        context['matches'] = matches
        return render(request, 'stock/product_sales_history.html', context)

    context['product'] = product
    ledger = build_stock_ledger(product)
    context['stock_ledger'] = ledger
    context['ledger_detailed'] = True  # merged Sales Detail + Stock Ledger view
    context['on_hand'] = sum(p.remaining for p in product.purchase_set.all())

    # Profit for the whole ledger (every sale, unfiltered) computed once and reused
    # both for the merged ledger rows and the filtered KPI totals below.
    ledger_sale_ids = [e['id'] for e in ledger['events'] if e['kind'] == 'sale']
    profit_map = sale_profit_map_for_sale_ids(ledger_sale_ids) if show_profit else {}
    if show_profit:
        for e in ledger['events']:
            if e['kind'] == 'sale':
                e['profit'] = profit_map.get(e['id'], {}).get('profit')

    sales_qs = (
        Sale.objects.filter(product=product)
        .annotate(business_at=Coalesce('order__created_at', 'date'))
        .select_related('order', 'order__store', 'order__customer', 'customer', 'store')
        .order_by('-business_at', '-id')
    )

    def _parse(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date()
        except Exception:
            return None

    sd, ed = _parse(start_str), _parse(end_str)
    if sd:
        sales_qs = sales_qs.filter(business_at__date__gte=sd)
    if ed:
        sales_qs = sales_qs.filter(business_at__date__lte=ed)
    if store_id.isdigit():
        sid = int(store_id)
        sales_qs = sales_qs.filter(Q(order__store_id=sid) | Q(order__isnull=True, store_id=sid))

    sales = list(sales_qs)

    rows, store_agg, month_agg, order_ids = [], {}, {}, set()
    total_qty, total_rev, total_profit = 0, Decimal('0.00'), Decimal('0.00')
    for s in sales:
        order = s.order
        line_total = (s.unit_price or Decimal('0.00')) * s.quantity
        store = order.store if (order and order.store_id) else s.store
        store_name = store.name if store else '—'
        cust = order.customer if (order and order.customer_id) else s.customer
        at = timezone.localtime(s.business_at) if s.business_at else None
        profit = profit_map.get(s.id, {}).get('profit', Decimal('0.00')) if show_profit else Decimal('0.00')
        rows.append({
            'at': at,
            'store': store_name,
            'order_id': order.id if order else None,
            'detail_url': reverse('sale_order_detail', args=[order.id]) if order else None,
            'customer': cust.name if cust else 'Walk-in / No customer',
            'customer_url': reverse('customer_detail', args=[cust.id]) if cust else None,
            'qty': s.quantity,
            'unit_price': s.unit_price,
            'line_total': line_total,
            'payment': payment_labels.get(s.payment_method, (s.payment_method or 'Other').title()),
            'profit': profit,
            'affects_stock': order.affects_stock if order else True,
        })
        total_qty += s.quantity
        total_rev += line_total
        total_profit += profit
        order_ids.add(order.id if order else f'legacy-{s.id}')
        st = store_agg.setdefault(store_name, {'qty': 0, 'rev': Decimal('0.00')})
        st['qty'] += s.quantity
        st['rev'] += line_total
        if at:
            mm = month_agg.setdefault(at.strftime('%Y-%m'), {'qty': 0, 'rev': Decimal('0.00')})
            mm['qty'] += s.quantity
            mm['rev'] += line_total

    context.update({
        'rows': rows,
        'total_qty': total_qty,
        'total_rev': total_rev,
        'total_profit': total_profit,
        'order_count': len(order_ids),
        'avg_unit': (total_rev / total_qty) if total_qty else Decimal('0.00'),
        'store_breakdown': sorted(
            ({'store': k, **v} for k, v in store_agg.items()), key=lambda r: -r['rev']),
        'month_breakdown': sorted(
            ({'month': k, **v} for k, v in month_agg.items()), key=lambda r: r['month'], reverse=True),
        'first_sale': rows[-1]['at'] if rows else None,
        'last_sale': rows[0]['at'] if rows else None,
    })
    return render(request, 'stock/product_sales_history.html', context)


@login_required
def home_view(request):
    """Where the Business tab, the brand and the login all land.

    A manager starts on Today - the day's numbers, orders, and what needs doing
    - with the month's analysis one tab away under Trends. An employee's own
    Today is the dashboard page, which only ever shows them the day's work.
    """
    if has_manager_access(request.user):
        return redirect('daily_summary')
    return redirect('dashboard')


def _qr_png_base64(text):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L,
                       box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    buffer = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


@login_required
@manager_required
def tools_view(request):
    """Occasional jobs that used to sit in the dashboard header."""
    return render(request, 'stock/tools.html')


@login_required
def phone_qr(request):
    """The QR that opens this system on a phone, as a PNG.

    Served on its own rather than inlined into every page: the side-menu
    dialog asks for it only when somebody opens it.
    """
    png = base64.b64decode(_qr_png_base64(request.build_absolute_uri('/')))
    response = HttpResponse(png, content_type='image/png')
    response['Cache-Control'] = 'private, max-age=3600'
    return response


@login_required
def download_db_backup(request):
    """Manager-only one-click download of a consistent SQLite snapshot.

    Lets the owner grab a full backup on demand (e.g. straight onto a USB by
    pointing the browser's download folder at it). Uses SQLite's online backup
    API against the live connection, so it's consistent even mid-write. The whole
    database is a single file, so this file *is* the backup — restoring is just
    putting it back and reloading."""
    if not has_manager_access(request.user):
        return redirect('dashboard')
    from django.db import connection
    if connection.vendor != 'sqlite':
        return HttpResponse('Backup is only supported on SQLite.', status=400)

    import sqlite3
    import tempfile
    import os
    fd, tmp_path = tempfile.mkstemp(suffix='.sqlite3')
    os.close(fd)
    try:
        connection.ensure_connection()
        target = sqlite3.connect(tmp_path)
        try:
            connection.connection.backup(target)
        finally:
            target.close()
        with open(tmp_path, 'rb') as fh:
            data = fh.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    resp = HttpResponse(data, content_type='application/octet-stream')
    resp['Content-Disposition'] = f'attachment; filename="scentory-db-{stamp}.sqlite3"'
    return resp


@login_required
def sync_all_perfumes_to_shopify(request):
    """Manager-only product-list button: sync every perfume to Shopify. The job
    (200+ products) runs in a detached background process so the request returns
    immediately; progress goes to logs/shopify_perfumes_sync.log."""
    if request.method != 'POST' or not has_manager_access(request.user):
        return redirect('product_list')

    from .services.shopify_client import ShopifyClient
    if not ShopifyClient().is_configured():
        messages.error(request, 'Shopify is not configured (SHOPIFY_ADMIN_TOKEN missing).')
        return redirect('product_list')

    import os
    import threading
    from django.conf import settings
    from django.core.management import call_command
    log_dir = os.path.join(settings.BASE_DIR, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, 'shopify_perfumes_sync.log')

    def _run():
        # Run the command in-process in a background thread (avoids the uwsgi
        # 'which python' problem of spawning a subprocess). Output -> log file.
        try:
            # buffering=1 -> line-buffered, so progress is visible live in the log
            # and survives if the worker is recycled mid-run.
            with open(log_path, 'a', encoding='utf-8', buffering=1) as f:
                f.write(f'\n=== sync started {timezone.now():%Y-%m-%d %H:%M} ===\n')
                # create=True is the point of the button: a perfume that is
                # not on Shopify is exactly what "sync all" is expected to put
                # there. Without it the command counted those as "missing" and
                # skipped them, so deleting a product and re-syncing did
                # nothing at all.
                call_command('sync_shopify_perfumes', apply=True, create=True,
                             stdout=f, stderr=f)
        except Exception as exc:  # noqa: BLE001 — record any crash to the log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(f'\nSync crashed: {exc}\n')
            except OSError:
                pass

    threading.Thread(target=_run, daemon=True).start()
    messages.success(
        request,
        'Syncing all perfumes to Shopify in the background. Perfumes not yet '
        'on Shopify are created and published, with their 10ml and 5ml sizes. '
        'This takes a few minutes — progress: logs/shopify_perfumes_sync.log')
    return redirect('product_list')


@login_required
def sync_product_to_shopify(request, pk):
    """Manager-only button on the product page: push this product to Shopify —
    create it if missing, then set its price + decant-aware inventory. Handy for
    listing a new perfume, or a one-off manual re-sync.

    Whether a product belongs online is Category.sync_to_shopify, the same
    switch the bulk sync commands read — accessories are shop-floor only."""
    product = get_object_or_404(Product, pk=pk)
    if request.method != 'POST' or not has_manager_access(request.user):
        return redirect('product_detail', pk=pk)
    if product.category and not product.category.sync_to_shopify:
        messages.error(request, f'{product.category.name} is not sold on Shopify.')
        return redirect('product_detail', pk=pk)

    from .services.shopify_client import ShopifyClient
    from .services import shopify_sync
    client = ShopifyClient()
    if not client.is_configured():
        messages.error(request, 'Shopify is not configured (SHOPIFY_ADMIN_TOKEN missing).')
        return redirect('product_detail', pk=pk)

    try:
        code, detail = shopify_sync.sync_product(product, client, create_missing=True, status='ACTIVE')
        if code == shopify_sync.SKIP_NO_BARCODE:
            messages.error(request, 'Product has no barcode — cannot match it on Shopify.')
            return redirect('product_detail', pk=pk)
        if code == shopify_sync.ERROR:
            messages.error(request, f'Shopify: {detail}')
            return redirect('product_detail', pk=pk)
        if code == shopify_sync.CREATED:
            messages.success(request, f'Created on Shopify: {detail}.')

        inv_code, inv_detail = shopify_sync.sync_product_price_inventory(
            product, client, do_price=True, do_inventory=True)
    except Exception as exc:  # keep the button from 500-ing on a Shopify hiccup
        messages.error(request, f'Shopify sync failed: {exc}')
        return redirect('product_detail', pk=pk)

    if inv_code == shopify_sync.INV_UPDATED:
        messages.success(request, f'Synced to Shopify — {inv_detail}.')
    elif inv_code == shopify_sync.INV_UNCHANGED:
        messages.info(request, 'Already up to date on Shopify.')
    elif inv_code == shopify_sync.INV_NOT_IN_SHOPIFY:
        messages.warning(request, 'Not found on Shopify to set price/inventory.')
    elif inv_code == shopify_sync.INV_ERROR:
        messages.error(request, f'Inventory sync: {inv_detail}')
    return redirect('product_detail', pk=pk)


@login_required
@manager_required
def edit_product_view(request, pk):
    product = get_object_or_404(
        Product.objects.select_related('category', 'brand_master', 'series_master'),
        pk=pk
    )

    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product,
                           can_edit_prices=has_manager_access(request.user))
        if form.is_valid():
            form.save()

            # ✅ 同时兼容 images / images[] 两种 name
            new_files = []
            new_files.extend(request.FILES.getlist('images'))
            new_files.extend(request.FILES.getlist('images[]'))

            saved_count = 0
            for f in new_files:
                if not f or not getattr(f, 'size', 0):
                    continue
                ProductImage.objects.create(product=product, image=f)
                saved_count += 1

            if saved_count:
                messages.success(request, f"Product saved. {saved_count} image(s) uploaded.")
            else:
                # 如果用户确实选择了文件，但后端没接到，多半是前端 name/表单问题
                if request.POST.get('had_image_selected') == '1':
                    messages.warning(
                        request,
                        "Product saved, but no images were received. Please re-select the files and try again."
                    )
                else:
                    messages.success(request, "Product saved.")

            # ✅ 保留当前 URL 上的查询串（包含 page、q、category 等）
            qs = request.META.get('QUERY_STRING', '')
            detail_url = reverse('product_detail', args=[pk])
            if qs:
                detail_url = f"{detail_url}?{qs}"
            return redirect(detail_url)
        else:
            messages.error(request, "Please fix the highlighted fields.")
    else:
        form = ProductForm(instance=product, can_edit_prices=has_manager_access(request.user))

    images = product.images.all()
    purchases = product.purchase_set.all().order_by('-date')
    purchase_count = product.purchase_set.count()
    sale_count = product.sale_set.count()
    # total available stock (sum of remaining from all batches)
    total_stock = purchases.aggregate(s=Coalesce(Sum('remaining'), Value(0), output_field=IntegerField()))['s'] or 0

    return render(request, 'stock/edit_product.html', {
        'form': form,
        'category_kind_json': json.dumps(build_category_kind_map()),
        'product': product,
        'images': images,
        'purchases': purchases,
        'total_stock': total_stock,
        'purchase_count': purchase_count,
        'sale_count': sale_count,
        'brand_series_map_json': json.dumps(build_brand_series_map()),
        'brand_catalog_json': json.dumps(build_brand_catalog()),
    })


@require_POST
@login_required
@manager_required
def toggle_restock(request, pk):
    """Switch a product's restock bell. Off keeps it out of low stock only."""
    from .services import low_stock
    product = get_object_or_404(Product, pk=pk)
    product.restock = not product.restock
    product.save(update_fields=['restock'])
    low_stock.forget()
    return JsonResponse({'id': product.id, 'restock': product.restock})


@require_POST
@login_required
@manager_required
def delete_product_view(request, pk):
    """Delete a product from the catalogue without destroying the books.

    A hard delete used to cascade the product away, taking its purchase and
    sales rows (and any order left empty) with it — so past revenue and profit
    changed retroactively. Instead the product is archived: it disappears from
    every catalogue view, search, scan and Shopify sync, while its sales stay
    exactly as they were, so history and profit are untouched. Photos are
    removed since nothing displays them any more."""
    product = get_object_or_404(Product.all_objects, pk=pk)
    sale_count = product.sale_set.count()

    with transaction.atomic():
        for image in list(product.images.all()):
            if image.image:
                image.image.delete(save=False)
        product.images.all().delete()
        product.archived_at = timezone.now()
        product.save(update_fields=['archived_at'])

    note = (f' Its {sale_count} sales record{"s" if sale_count != 1 else ""} '
            f'and their profit are kept.') if sale_count else ''
    messages.success(request, f'Deleted "{product.display_name}".{note}')
    return _back_to(request, 'product_list', avoid=(reverse('product_detail', args=[pk]),))


@login_required
def dashboard_view(request):
    isManger = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    show_order_financials = has_order_reconciliation_access(request.user)
    show_profit = bool(request.user.is_superuser)

    active_store, store_is_all = resolve_active_store(request)
    store_scope = None if store_is_all else active_store
    store_cache_key = store_scope.id if store_scope else 'all'

    today = timezone.localdate()
    selected_cat_ids = request.GET.getlist('cat')
    selected_cat_ids_int = [int(x) for x in selected_cat_ids if str(x).isdigit()]

    all_categories = Category.objects.order_by('name')
    perfume_category = (
        all_categories
        .filter(Q(name__iexact='Perfumes') | Q(name__icontains='perfume'))
        .first()
    )
    dashboard_defaulted_to_perfume = False
    if not selected_cat_ids_int and perfume_category:
        selected_cat_ids_int = [perfume_category.id]
        dashboard_defaulted_to_perfume = True
    categories = all_categories
    selected_cats = list(categories.filter(id__in=selected_cat_ids_int))

    month_context = resolve_dashboard_month(request.GET.get('month', '').strip(), today)

    # Short-TTL cache for the monthly snapshot (the heavy part: FIFO profit replay,
    # inventory/AR scans). Today's live order list below is computed fresh, so the
    # ~60s staleness only affects the monthly overview, not today's operations.
    cache_scope = ','.join(str(cid) for cid in sorted(selected_cat_ids_int))
    snapshot_cache_key = (
        f"dash:snap:{month_context['month_value']}:{cache_scope}:"
        f"{int(show_profit)}:{month_context['period_end'].isoformat()}:store{store_cache_key}"
    )
    monthly_overview = cache.get(snapshot_cache_key)
    if monthly_overview is None:
        monthly_overview = build_monthly_dashboard_snapshot(
            month_start=month_context['month_start'],
            period_end=month_context['period_end'],
            selected_category_ids=selected_cat_ids_int,
            show_profit=show_profit,
            store=store_scope,
        )
        cache.set(snapshot_cache_key, monthly_overview, 300)

    current_headline = {
        'sales_amount': monthly_overview['month_sales_amount'],
        'profit': monthly_overview['month_sales_profit'],
        'order_count': monthly_overview['month_order_count'],
        'avg_ticket': monthly_overview['month_avg_ticket'],
    }
    comparison_cache_key = f"dash:cmp:{month_context['month_value']}:{cache_scope}:{int(show_profit)}:{month_context['period_end'].isoformat()}:store{store_cache_key}"
    mom_comparison = cache.get(comparison_cache_key)
    if mom_comparison is None:
        mom_comparison = build_period_comparison(
            month_context, current_headline,
            selected_category_ids=selected_cat_ids_int,
            show_profit=show_profit,
            store=store_scope,
        )
        cache.set(comparison_cache_key, mom_comparison, 300)

    target_progress = build_target_progress(
        month_context, monthly_overview['month_sales_amount'],
        selected_category_ids=selected_cat_ids_int,
    )

    def build_dashboard_url(month_value=None, category_ids=None):
        params = []
        if month_value:
            params.append(('month', month_value))
        for category_id in category_ids or []:
            params.append(('cat', str(category_id)))
        query = urlencode(params, doseq=True)
        base_url = reverse('dashboard')
        return f'{base_url}?{query}' if query else base_url

    for point in monthly_overview['chart_data']:
        point['detail_url'] = (
            f"{reverse('sales_records')}?"
            f"{urlencode({'start_date': point['date'].isoformat(), 'end_date': point['date'].isoformat()})}"
        )

    selected_cat_chips = []
    for category in selected_cats:
        remaining_ids = [cat_id for cat_id in selected_cat_ids_int if cat_id != category.id]
        selected_cat_chips.append({
            'name': category.name,
            'remove_url': build_dashboard_url(month_context['month_value'], remaining_ids),
        })

    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    # Employees have no access to the manager-only /today page, so give them
    # today's sales here. Managers keep the clean dashboard + /today, so this is
    # only computed for employees (no profit replay — they never see profit).
    sale_orders_today = []
    today_sales_qty = 0
    total_sales_today = Decimal('0.00')
    today_payment_breakdown = []
    if not isManger:
        orders_qs = (
            SaleOrder.objects
            .filter(created_at__date=today)
            .select_related('customer')
            .prefetch_related('items__product', 'items__product__images', 'payments', 'charges')
            .order_by('-created_at')
            .distinct()
        )
        orders_qs = scope_sales_by_store(orders_qs, store_scope, store_is_all)
        if selected_cat_ids_int:
            orders_qs = orders_qs.filter(items__product__category_id__in=selected_cat_ids_int).distinct()

        today_payment_totals = defaultdict(Decimal)
        today_payment_qty = defaultdict(int)
        for order in orders_qs:
            view_total_qty = 0
            view_total_amount = Decimal('0.00')
            items_today = []
            for item in order.items.all():
                if selected_cat_ids_int and item.product.category_id not in selected_cat_ids_int:
                    continue
                item.line_total = (item.unit_price or Decimal('0.00')) * item.quantity
                item.payment_label = payment_labels.get(item.payment_method, (item.payment_method or 'Other').title())
                item.image_url = get_product_image_url(item.product)
                items_today.append(item)
                view_total_qty += item.quantity
                view_total_amount += item.line_total
                today_sales_qty += item.quantity
                total_sales_today += item.line_total
                today_payment_qty[item.payment_method] += item.quantity
            if items_today:
                # Order-level tender (split payments), scaled to the filtered subtotal.
                for method, amount in order_tender_amounts(order, view_total_amount, items_today).items():
                    today_payment_totals[method] += amount
                order.view_total_qty = view_total_qty
                order.view_total_amount = view_total_amount
                order.view_items_today = items_today
                sale_orders_today.append(order)

        for code, amount in sorted(today_payment_totals.items(), key=lambda kv: (-kv[1], kv[0] or '')):
            today_payment_breakdown.append({
                'code': code,
                'label': payment_labels.get(code, (code or 'Other').title()),
                'amount': amount,
                'qty': today_payment_qty.get(code, 0),
                'share_pct': (amount / total_sales_today * Decimal('100')) if total_sales_today else Decimal('0.00'),
            })

    # Low stock: shared with the alerts panel (services/low_stock.py).
    low_data = low_stock_data(selected_cat_ids_int)
    low_stock_count = low_data['count']
    out_of_stock_count = low_data['out']
    low_stock_1_2_count = low_data['low12']
    low_stock_3_4_count = low_data['low34']
    low_stock_focus_products = low_data['focus']

    prod_for_totals = Product.objects.all()
    if selected_cat_ids_int:
        prod_for_totals = prod_for_totals.filter(category_id__in=selected_cat_ids_int)

    total_products = prod_for_totals.count()
    total_stock = monthly_overview['current_stock_units']


    return render(request, 'stock/dashboard.html', {
        'categories': categories,
        'selected_cat_ids': [str(i) for i in selected_cat_ids_int],
        'selected_cats': selected_cats,
        'selected_cat_chips': selected_cat_chips,
        'selected_month': month_context['month_value'],
        'month_label': month_context['month_label'],
        'month_scope_label': month_context['scope_label'],
        'period_start': month_context['month_start'],
        'period_end': month_context['period_end'],
        'prev_month_url': build_dashboard_url(month_context['prev_month_value'], selected_cat_ids_int),
        'current_month_url': build_dashboard_url(today.strftime('%Y-%m'), selected_cat_ids_int),
        'next_month_url': build_dashboard_url(month_context['next_month_value'], selected_cat_ids_int) if month_context['next_month_value'] else '',
        'sales_records_month_url': (
            f"{reverse('sales_records')}?"
            f"{urlencode({'start_date': month_context['month_start'].isoformat(), 'end_date': month_context['period_end'].isoformat()})}"
        ),
        'today': today,
        'today_sales_qty': today_sales_qty,
        'today_order_count': len(sale_orders_today),
        'total_sales_today': total_sales_today,
        'today_payment_breakdown': today_payment_breakdown,
        'sale_orders_today': sale_orders_today,
        'low_stock_focus_products': low_stock_focus_products,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'low_stock_1_2_count': low_stock_1_2_count,
        'low_stock_3_4_count': low_stock_3_4_count,
        'total_products': total_products,
        'total_stock': total_stock,
        'isManger': isManger,
        'dashboard_defaulted_to_perfume': dashboard_defaulted_to_perfume,
        'dashboard_default_category_name': perfume_category.name if perfume_category else '',
        'employee_daily_only': not isManger,
        'show_purchase_metrics': isManger,
        'show_sales_sensitive': show_sales_sensitive,
        'show_order_financials': show_order_financials,
        'show_profit': show_profit,
        'ar_scope_note': bool(selected_cat_ids_int),
        'mom_comparison': mom_comparison,
        'target_progress': target_progress,
        'store_is_all': store_is_all,
        **monthly_overview,
    })


@login_required
def yearly_sales_view(request):
    """Sales Trend was merged into the Sales records page (its no-range state is
    the yearly overview). Keep this route as a redirect for old links."""
    params = []
    year_value = (request.GET.get('year') or '').strip()
    if year_value:
        params.append(('year', year_value))
    for category_id in request.GET.getlist('cat'):
        params.append(('cat', str(category_id)))
    query = urlencode(params, doseq=True)
    base_url = reverse('sales_records')
    return redirect(f'{base_url}?{query}' if query else base_url)


def _lookup_scope(request, queryset):
    """Narrow a product lookup to what the active store sells.

    Selling is store-specific, but stock is not: inbound receives for the
    whole company, so it asks for ``scope=stock`` and gets the full catalogue.
    Everything else is a till lookup and is scoped by default.
    """
    if request.GET.get('scope') == 'stock':
        return queryset
    active_store, _is_all = resolve_active_store(request)
    return scope_products_by_store(queryset, active_store)


@login_required
def check_barcode(request):
    barcode = request.GET.get('barcode', '').strip()
    try:
        qs = _lookup_scope(
            request,
            Product.objects.select_related('brand_master', 'series_master').prefetch_related('images'))
        product = qs.get(barcode=barcode)
        # 当前库存（可用不用于表单，仅供前端显示时参考；这里不必返回也可）
        stock = product.purchase_set.filter(remaining__gt=0).aggregate(s=Sum('remaining'))['s'] or 0
        # 最近一次进价
        last_purchase = product.purchase_set.order_by('-date').first()
        last_cost = last_purchase.cost_price if last_purchase else None
        image_url = ''
        for image in product.images.all():
            try:
                image_url = image.image.url
            except ValueError:
                image_url = ''
            break
        display_name = product.display_name

        return JsonResponse({
            'exists': True,
            'name': display_name,
            'display_name': display_name,
            'brand': product.brand,
            'model': product.model or '',
            'product_name': product.name,
            'spec': product.spec or '',
            'color': product.color or '',
            'stock': int(stock),
            'last_cost': float(last_cost) if last_cost is not None else None,
            'retail_price': float(product.default_price) if product.default_price is not None else None,
            'wholesale_price': float(product.wholesale_price) if product.wholesale_price is not None else None,
            'image_url': image_url,
        })
    except Product.DoesNotExist:
        return JsonResponse({'exists': False})



# -----------------------------
# 销售汇总列表/详情
# -----------------------------

def _order_for_user(request, order_id):
    """The order, if this user may see it: employees only their store's."""
    order_qs = SaleOrder.objects.select_related('customer', 'store')
    if not has_manager_access(request.user):
        active_store, store_is_all = resolve_active_store(request)
        order_qs = scope_sales_by_store(order_qs, active_store, store_is_all)
    return get_object_or_404(order_qs, id=order_id)


@login_required
def sale_order_receipt_pdf(request, order_id):
    """The A4 receipt, as a PDF in the quote's look, opened in the browser to
    print or share. Payments come from the order's own split, as on Today."""
    from .services import receipt_pdf
    order = _order_for_user(request, order_id)
    items = list(order.items.select_related('product').order_by('id'))
    for item in items:
        item.line_total = (item.unit_price or Decimal('0.00')) * item.quantity
    total = sum((item.line_total for item in items), Decimal('0.00'))
    pay = order_tender_amounts(order, total, items)
    payments = [(method, amount) for method, amount in
                sorted(pay.items(), key=lambda kv: (-kv[1], kv[0] or '')) if method and amount]
    charges = list(order.charges.all())

    customer = order.customer
    profile = PrintProfile.get_for_store(order.store)
    pdf = receipt_pdf.build_receipt_pdf(
        profile=profile,
        order_id=order.id,
        created_label=timezone.localtime(order.created_at).strftime('%d/%m/%Y %H:%M'),
        store_name=order.store.name if order.store_id else '',
        customer_name=customer.name if customer else '',
        customer_phone=(customer.phone or '') if customer else '',
        customer_nif=(customer.nif or '') if customer else '',
        lines=[{'title': build_product_label(item.product),
                'barcode': item.product.barcode if item.product else '',
                'quantity': item.quantity, 'unit_price': item.unit_price or Decimal('0.00'),
                'line_total': item.line_total} for item in items],
        total=total + sum((c.amount for c in charges), Decimal('0.00')),
        payments=payments,
        footer_note=profile.footer_note,
        charges=[(c.label, c.amount) for c in charges],
    )
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="recibo-{order.id}.pdf"'
    return response


@login_required
def sale_order_detail_view(request, order_id):
    order = _order_for_user(request, order_id)
    # The A4 print is a PDF now; old "?print=1&layout=a4" links land on it.
    if request.GET.get('print') == '1' and request.GET.get('layout') != 'pos':
        return redirect('sale_order_receipt_pdf', order_id=order.id)
    items = list(
        order.items
        .select_related('product', 'product__brand_master', 'product__series_master')
        .order_by('id')
    )

    show_sensitive = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    show_order_financials = has_order_reconciliation_access(request.user)
    can_print_receipt = request.user.is_authenticated
    print_mode = request.GET.get('print') == '1'
    print_layout = 'pos' if request.GET.get('layout') == 'pos' else 'a4'
    show_receipt_amounts = show_order_financials

    total_qty = sum(i.quantity for i in items)
    total_amount = sum((i.unit_price * i.quantity for i in items), Decimal('0.00'))

    for i in items:
        i.display_name = build_product_label(i.product)
        i.line_total = (i.unit_price or Decimal('0.00')) * i.quantity
        i.image_url = get_product_image_url(i.product)
    # The order's own payment split (as Today and the receipt read it), which
    # covers shipping and fees too; it used to add up each line's method.
    pay_break = {m: a for m, a in sorted(order_tender_amounts(order, total_amount, items).items(),
                                         key=lambda kv: (-kv[1], kv[0] or '')) if m and a}
    charges = list(order.charges.all())
    charges_total = sum((c.amount for c in charges), Decimal('0.00'))

    shop_profile = PrintProfile.get_for_store(order.store)
    back_url = request.GET.get('next', '').strip() or reverse('sale_order_detail', args=[order.id])

    context = {
        'order': order,
        'items': items,
        'total_qty': total_qty,
        'total_amount': total_amount,
        'charges': charges,
        'grand_total': total_amount + charges_total,
        'pay_break': dict(pay_break),
        'can_admin_correct': has_admin_access(request.user),
        'can_print_receipt': can_print_receipt,
        'print_mode': print_mode,
        'print_layout': print_layout,
        'show_receipt_amounts': show_receipt_amounts,
        'show_sensitive': show_sensitive,
        'show_sales_sensitive': show_sales_sensitive,
        'show_order_financials': show_order_financials,
        'print_a4_url': reverse('sale_order_receipt_pdf', args=[order.id]),
        'print_pos_url': f"{reverse('sale_order_detail', args=[order.id])}?print=1&layout=pos",
        'edit_print_profile_url': f"{reverse('print_profile_edit')}?{urlencode({'next': back_url})}",
        'shop': {
            'name': shop_profile.name,
            'nif': shop_profile.nif,
            'phone': shop_profile.phone,
            'address': shop_profile.address,
            'email': shop_profile.email,
            'footer_note': shop_profile.footer_note,
        },
    }
    return render(request, 'stock/sale_order_detail.html', context)


@login_required
@admin_required
def audit_view(request):
    """Settings -> Audit opens on its first page."""
    return redirect('audit_orders')


def _audit_page(request, page, template, rows=None, **extra):
    """Render one Audit page: the tabs, the period and search, 50 rows a page."""
    from .services import audit
    context = {
        'page': page,
        'pages': audit.PAGES,
        'periods': audit.PERIODS,
        'period': extra.pop('period', None),
        'query': extra.pop('query', ''),
        'open_errors': audit.open_error_count(),
    }
    if rows is not None:
        context['page_obj'] = Paginator(rows, 50).get_page(request.GET.get('page'))
    context.update(extra)
    return render(request, template, context)


def _audit_filters(request):
    from .services import audit
    period = request.GET.get('period', '30')
    if period not in dict(audit.PERIODS):
        period = '30'
    return period, audit.since_for(period), (request.GET.get('q') or '').strip()


@login_required
@admin_required
def audit_orders_view(request):
    from .services import audit
    period, since, query = _audit_filters(request)
    return _audit_page(request, 'audit_orders', 'stock/audit_feed.html', audit.order_changes(since, query),
                       period=period, query=query, search_hint='Order #, reason, user')


@login_required
@admin_required
def audit_admin_view(request):
    from .services import audit
    period, since, query = _audit_filters(request)
    return _audit_page(request, 'audit_admin', 'stock/audit_feed.html', audit.admin_edits(since, query),
                       period=period, query=query, search_hint='Record, type, user')


@login_required
@admin_required
def audit_stock_view(request):
    from .services import audit
    period, since, query = _audit_filters(request)
    return _audit_page(request, 'audit_stock', 'stock/audit_stock.html', audit.stock_counts(since, query),
                       period=period, query=query, search_hint='Product, EAN, user')


@login_required
@admin_required
def audit_deliveries_view(request):
    from .services import audit
    period, since, query = _audit_filters(request)
    return _audit_page(request, 'audit_deliveries', 'stock/audit_deliveries.html',
                       audit.deliveries(since, query),
                       period=period, query=query, search_hint='Inbound #, supplier, invoice')


@login_required
@admin_required
def audit_accounts_view(request):
    from .services import audit
    period, since, _ = _audit_filters(request)
    return _audit_page(request, 'audit_accounts', 'stock/audit_accounts.html',
                       period=period, accounts=audit.accounts(), failed=audit.failed_logins(since))


@login_required
@admin_required
def audit_errors_view(request):
    from .services import audit
    period, since, query = _audit_filters(request)
    status = request.GET.get('status', 'open')
    if status not in ('open', 'resolved', 'all'):
        status = 'open'
    return _audit_page(request, 'audit_errors', 'stock/audit_errors.html', audit.errors(since, query, status),
                       period=period, query=query, status=status, search_hint='Message, page, source')


@login_required
@admin_required
@require_POST
def audit_error_resolve(request, error_id):
    """Mark an error dealt with; it comes back if it happens again."""
    from django.utils.http import url_has_allowed_host_and_scheme
    from .models import ErrorLog
    ErrorLog.objects.filter(pk=error_id).update(resolved_at=timezone.now())
    back = request.POST.get('next') or ''
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse('audit_errors')
    return redirect(back)


@login_required
@admin_required
def business_settings_view(request):
    """Settings for the whole business: the card fee rate."""
    from .models import BusinessSettings
    settings_row = BusinessSettings.get()
    error = ''
    if request.method == 'POST':
        raw = (request.POST.get('card_fee_pct') or '').strip().replace(',', '.')
        try:
            value = Decimal(raw)
            if value < 0 or value > 10:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            error = 'Enter a percentage between 0 and 10, e.g. 1 or 1.5.'
        else:
            settings_row.card_fee_pct = value.quantize(Decimal('0.01'))
            settings_row.save(update_fields=['card_fee_pct', 'updated_at'])
            messages.success(request, f'Card fee set to {settings_row.card_fee_pct}% for new card sales.')
            return redirect('business_settings')
    return render(request, 'stock/business_settings.html', {'settings_row': settings_row, 'error': error})


@login_required
@admin_required
def print_profile_edit_view(request):
    active_store, store_is_all = resolve_active_store(request)
    target_store = active_store or Store.get_default()
    profile = PrintProfile.get_for_store(target_store)
    next_url = request.GET.get('next', '').strip() or request.POST.get('next', '').strip() or reverse('dashboard')

    if request.method == 'POST':
        form = PrintProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, f'Print header updated for {target_store.name if target_store else "the shop"}.')
            return redirect(next_url)
        messages.error(request, 'Please fix the highlighted fields.')
    else:
        form = PrintProfileForm(instance=profile)

    return render(request, 'stock/print_profile_form.html', {
        'form': form,
        'next_url': next_url,
        'target_store': target_store,
        'store_is_all': store_is_all,
    })


def _correction_cart_item(product, *, qty, price, payment, is_split=False, payment_split=None):
    return {
        'product_id': product.id,
        'barcode': product.barcode,
        'title': build_product_label(product),
        'image_url': get_product_image_url(product),
        'retail': float(product.default_price) if product.default_price is not None else None,
        'wholesale': float(product.wholesale_price) if product.wholesale_price is not None else None,
        'qty': qty,
        'price': price,
        'payment': payment,
        'isSplit': is_split,
        'paymentSplit': payment_split,
    }


def _build_correction_cart(order):
    """Rich cart payload (with product info) preloaded into the JS cart on edit.

    The authoritative payment split lives in the order-level ``SaleOrderPayment``
    rows (each ``Sale`` line only stores its *primary* method). Reconstruct a
    valid per-line split by greedily allocating the order-level payment pool
    across the lines, so a split order reloads — and re-saves — without
    collapsing back to a single method.
    """
    if not order:
        return []

    valid_methods = ('cash', 'card', 'mbway')
    lines = [
        item for item in order.items.select_related('product').order_by('id')
        if item.product
    ]

    pool = defaultdict(Decimal)
    for payment in order.payments.all():
        if payment.method in valid_methods and payment.amount:
            pool[payment.method] += payment.amount
    # Shipping and fees were paid from the same pool; take them out first so
    # the lines get back only what was paid for goods.
    for charge in order.charges.all():
        if charge.payment_method in pool:
            pool[charge.payment_method] = max(pool[charge.payment_method] - charge.amount, Decimal('0'))
    if not any(pool.values()):
        pool = defaultdict(Decimal)
    if not pool:
        # No order-level tender recorded: fall back to each line's own method.
        for item in lines:
            method = item.payment_method if item.payment_method in valid_methods else 'cash'
            pool[method] += (item.unit_price or Decimal('0')) * item.quantity

    cart = []
    for item in lines:
        remaining = (item.unit_price or Decimal('0')) * item.quantity
        line_map = {}
        for method in valid_methods:
            if remaining <= 0:
                break
            available = pool.get(method, Decimal('0'))
            if available <= 0:
                continue
            take = min(available, remaining)  # each method is visited once per line
            line_map[method] = take
            pool[method] = available - take
            remaining -= take
        if remaining > Decimal('0.01'):
            # Pool underflow (data drift): cover the rest with the line's method.
            fallback = item.payment_method if item.payment_method in valid_methods else 'cash'
            line_map[fallback] = line_map.get(fallback, Decimal('0')) + remaining

        methods_used = [m for m in valid_methods if line_map.get(m, Decimal('0')) > 0]
        is_split = len(methods_used) > 1
        payment_split = {m: float(round(line_map[m], 2)) for m in methods_used} if is_split else None
        if methods_used:
            primary = max(methods_used, key=lambda m: line_map[m])
        else:
            primary = item.payment_method if item.payment_method in valid_methods else 'cash'

        cart.append(_correction_cart_item(
            item.product, qty=item.quantity, price=float(item.unit_price),
            payment=primary, is_split=is_split, payment_split=payment_split,
        ))
    return cart


def _enrich_correction_items(raw_items):
    """Rebuild the rich cart from submitted minimal items (used on validation error)."""
    ids = []
    for row in raw_items:
        try:
            ids.append(int(row.get('product_id')))
        except (TypeError, ValueError):
            continue
    products = {p.id: p for p in Product.all_objects.filter(id__in=ids)}
    cart = []
    for row in raw_items:
        try:
            product = products[int(row.get('product_id'))]
            qty = int(row.get('qty', 0))
            price = float(row.get('price', 0))
        except (TypeError, ValueError, KeyError):
            continue
        cart.append(_correction_cart_item(product, qty=qty, price=price, payment=str(row.get('payment', '')).strip()))
    return cart


def _parse_correction_cart(request):
    """Parse + validate items_json/payments_json from the correction POST.

    Returns ``(line_items, payment_totals, order_revenue, charges)`` mirroring
    the POS outbound contract; payments cover goods plus charges. Raises
    ``ValueError`` with a user-facing message.
    """
    valid_methods = {'cash', 'card', 'mbway'}
    method_priority = {'cash': 0, 'card': 1, 'mbway': 2}

    try:
        items = json.loads(request.POST.get('items_json') or '[]')
    except (ValueError, TypeError):
        raise ValueError('Could not read the order lines.')
    if not isinstance(items, list) or not items:
        raise ValueError('Add at least one product line before saving.')

    parsed = []
    order_revenue = Decimal('0.00')
    line_payment_totals = defaultdict(Decimal)
    for row in items:
        try:
            product_id = int(row.get('product_id'))
            qty = int(row.get('qty', 0))
            price = Decimal(str(row.get('price', 0)))
        except (TypeError, ValueError, ArithmeticError):
            raise ValueError('Quantity and price must be numbers.')
        if qty < 1 or price <= 0:
            raise ValueError('Each line needs a product, quantity of at least 1, and a price above 0.')
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            raise ValueError('A selected product no longer exists.')
        line_payment = str(row.get('payment', '')).strip()
        line_total = price * qty
        order_revenue += line_total
        if line_payment in valid_methods:
            line_payment_totals[line_payment] += line_total
        parsed.append({'product': product, 'quantity': qty, 'unit_price': price, 'payment': line_payment})

    try:
        raw_payments = json.loads(request.POST.get('payments_json') or '[]')
    except (ValueError, TypeError):
        raw_payments = []

    payment_totals = defaultdict(Decimal)
    if isinstance(raw_payments, list) and raw_payments:
        for entry in raw_payments:
            method = str(entry.get('method', '')).strip()
            try:
                amount = Decimal(str(entry.get('amount', 0)))
            except (TypeError, ValueError, ArithmeticError):
                raise ValueError('Payment amount must be a number.')
            if method in valid_methods and amount > 0:
                payment_totals[method] += amount
    elif line_payment_totals:
        payment_totals = line_payment_totals

    if not payment_totals:
        raise ValueError('Choose a payment method for the order.')

    charges = order_charges.parse_charges(request.POST.get('charges_json'))
    due = order_revenue + order_charges.charges_total(charges)
    paid_total = sum(payment_totals.values(), Decimal('0.00'))
    if abs(paid_total - due) > Decimal('0.01'):
        raise ValueError(f'Payments (EUR {paid_total:.2f}) must add up to the order total (EUR {due:.2f}).')

    primary_method = min(payment_totals.items(), key=lambda kv: (-kv[1], method_priority.get(kv[0], 9)))[0]
    line_items = [
        {
            'product': row['product'],
            'quantity': row['quantity'],
            'unit_price': row['unit_price'],
            'payment_method': row['payment'] if row['payment'] in valid_methods else primary_method,
        }
        for row in parsed
    ]
    return line_items, dict(payment_totals), order_revenue, charges


def _correction_charges(order, request=None):
    """Charges the form starts with: the submitted ones after a refused save,
    otherwise the order's own."""
    if request is not None and request.method == 'POST':
        try:
            rows = json.loads(request.POST.get('charges_json') or '[]')
            if isinstance(rows, list):
                return [{'label': str(r.get('label') or ''), 'amount': str(r.get('amount') or ''),
                         'payment': str(r.get('payment') or '')}
                        for r in rows if isinstance(r, dict)]
        except (TypeError, ValueError):
            pass
    if not order:
        return []
    return [{'label': c.label, 'amount': f'{c.amount:.2f}', 'payment': c.payment_method}
            for c in order.charges.all()]


@login_required
@admin_required
def sale_order_correction_center_view(request):
    query = request.GET.get('q', '').strip()
    start_date = parse_date((request.GET.get('start_date') or '').strip())
    end_date = parse_date((request.GET.get('end_date') or '').strip())

    active_store, store_is_all = resolve_active_store(request)
    orders_qs = scope_sales_by_store(
        SaleOrder.objects
        .select_related('customer', 'store')
        .prefetch_related('items')
        .order_by('-created_at', '-id'),
        active_store, store_is_all,
    )

    if query:
        filters = (
            Q(customer__name__icontains=query) |
            Q(customer__nif__icontains=query) |
            Q(note__icontains=query)
        )
        if query.isdigit():
            filters |= Q(id=int(query))
        orders_qs = orders_qs.filter(filters)

    if start_date:
        orders_qs = orders_qs.filter(created_at__date__gte=start_date)
    if end_date:
        orders_qs = orders_qs.filter(created_at__date__lte=end_date)

    paginator = Paginator(orders_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Group the current page's orders by day (newest first) so the list reads as
    # dated sections rather than one flat run; each day carries a small subtotal.
    order_date_groups = []
    current_group = None
    for order in page_obj.object_list:
        items = list(order.items.all())
        order.view_total_qty = sum(item.quantity for item in items)
        order.view_total_amount = sum((item.quantity * item.unit_price for item in items), Decimal('0.00'))
        order.view_store = order.store.name if order.store_id else '—'

        order_day = timezone.localtime(order.created_at).date()
        if current_group is None or current_group['date'] != order_day:
            current_group = {'date': order_day, 'orders': [], 'count': 0, 'amount': Decimal('0.00')}
            order_date_groups.append(current_group)
        current_group['orders'].append(order)
        current_group['count'] += 1
        current_group['amount'] += order.view_total_amount

    # A day heading carries the whole day, not just the part on this page: a
    # day split across two pages used to show two different partial totals.
    page_days = [group['date'] for group in order_date_groups]
    if page_days:
        day_totals = {}
        day_orders = (orders_qs.filter(created_at__date__in=page_days)
                      .prefetch_related('items'))
        for order in day_orders:
            day = timezone.localtime(order.created_at).date()
            bucket = day_totals.setdefault(day, {'count': 0, 'amount': Decimal('0.00')})
            bucket['count'] += 1
            bucket['amount'] += sum((item.quantity * item.unit_price
                                     for item in order.items.all()), Decimal('0.00'))
        for group in order_date_groups:
            whole = day_totals.get(group['date'])
            if whole:
                group['count'], group['amount'] = whole['count'], whole['amount']

    recent_logs = (
        SaleOrderChangeLog.objects
        .select_related('changed_by', 'order')
        .order_by('-created_at', '-id')[:12]
    )

    return render(request, 'stock/sale_order_correction_center.html', {
        'page_obj': page_obj,
        'order_date_groups': order_date_groups,
        'store_is_all': store_is_all,
        'recent_logs': recent_logs,
        'query': query,
        'start_date': request.GET.get('start_date', ''),
        'end_date': request.GET.get('end_date', ''),
    })


def _sale_order_correction_view(request, order=None):
    order = order or None
    initial_cart = _build_correction_cart(order)

    if request.method == 'POST':
        form = SaleOrderCorrectionForm(request.POST, instance=order)

        if order and 'delete_order' in request.POST:
            order_pk = order.pk
            reason = (request.POST.get('reason') or '').strip()
            if not reason:
                form.add_error('reason', 'Reason is required before deleting an order.')
            if not form.errors:
                try:
                    delete_sale_order_correction(
                        order=order,
                        changed_by=request.user,
                        reason=reason,
                    )
                except ValidationError as exc:
                    form.add_error(None, '; '.join(exc.messages))
                else:
                    messages.success(request, f'Order #{order.id} deleted and stock restored.')
                    return _back_to(request, 'sale_order_correction_center',
                                    avoid=(reverse('sale_order_detail', args=[order_pk]),
                                           reverse('sale_order_correction_edit', args=[order_pk])))
        else:
            # Preserve the submitted cart so a validation error does not wipe the work.
            try:
                submitted_raw = json.loads(request.POST.get('items_json') or '[]')
                if isinstance(submitted_raw, list) and submitted_raw:
                    initial_cart = _enrich_correction_items(submitted_raw)
            except (ValueError, TypeError):
                pass

            if form.is_valid():
                try:
                    line_items, payment_totals, _, charges = _parse_correction_cart(request)
                    _active_stores = available_stores()
                    _raw_store = request.POST.get('store')
                    selected_store = next(
                        (s for s in _active_stores if str(s.id) == str(_raw_store)), None
                    )
                    if selected_store is None:
                        selected_store = (order.store if (order and order.store_id) else None) or store_for_new_sale(request)
                    affects_stock = ('affects_stock' in request.POST) if order is None else None
                    saved_order = save_sale_order_correction(
                        order=order,
                        customer=form.cleaned_data.get('customer'),
                        note=form.cleaned_data.get('note'),
                        order_datetime=form.cleaned_data['order_datetime'],
                        line_items=line_items,
                        payment_totals=payment_totals,
                        changed_by=request.user,
                        reason=form.cleaned_data['reason'],
                        store=selected_store,
                        affects_stock=affects_stock,
                        charges=charges,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                except ValidationError as exc:
                    form.add_error(None, '; '.join(exc.messages))
                else:
                    action_label = 'created' if order is None else 'updated'
                    messages.success(request, f'Order #{saved_order.id} {action_label}.')
                    return redirect('sale_order_correction_edit', order_id=saved_order.id)
            else:
                messages.error(request, 'Please fix the highlighted fields.')
    else:
        form = SaleOrderCorrectionForm(instance=order)

    change_logs = []
    if order and order.pk:
        change_logs = order.change_logs.select_related('changed_by').order_by('-created_at', '-id')[:12]

    stores_for_template = available_stores()
    _raw_selected = request.POST.get('store') if request.method == 'POST' else None
    selected_store_id = None
    if _raw_selected:
        _match = next((s for s in stores_for_template if str(s.id) == str(_raw_selected)), None)
        selected_store_id = _match.id if _match else None
    if selected_store_id is None:
        _default_store = (order.store if (order and order.store_id) else None) or store_for_new_sale(request)
        selected_store_id = _default_store.id if _default_store else None

    affects_stock_checked = ('affects_stock' in request.POST) if request.method == 'POST' else True

    return render(request, 'stock/sale_order_correction_form.html', {
        'form': form,
        'order': order,
        'is_create': order is None,
        'change_logs': change_logs,
        'initial_cart': initial_cart,
        'initial_charges': _correction_charges(order, request),
        'store_options': stores_for_template,
        'selected_store_id': selected_store_id,
        'affects_stock_checked': affects_stock_checked,
    })


@login_required
@admin_required
def sale_order_correction_create_view(request):
    return _sale_order_correction_view(request, order=None)


@login_required
@admin_required
def sale_order_correction_edit_view(request, order_id):
    order = get_object_or_404(
        SaleOrder.objects.select_related('customer').prefetch_related('items__product'),
        id=order_id,
    )
    return _sale_order_correction_view(request, order=order)


@login_required
@manager_required
def supplier_list_view(request):
    query = (request.GET.get('q') or '').strip()
    country_filter = (request.GET.get('country') or '').strip()
    suppliers_qs = (
        Supplier.objects
        .prefetch_related('product_types')
        .annotate(
            purchase_count=Count('purchase', distinct=True),
            inbound_count=Count('inboundorder', distinct=True),
            last_purchase_at=Max('purchase__date'),
        )
        .order_by('country', 'name')
    )

    if query:
        suppliers_qs = suppliers_qs.filter(
            Q(name__icontains=query) |
            Q(phone__icontains=query) |
            Q(country__icontains=query) |
            Q(address__icontains=query) |
            Q(product_types__name__icontains=query)
        ).distinct()
    if country_filter == '__none__':
        suppliers_qs = suppliers_qs.filter(Q(country__isnull=True) | Q(country=''))
    elif country_filter:
        suppliers_qs = suppliers_qs.filter(country=country_filter)

    suppliers = list(suppliers_qs)
    # Lead-time comparison across suppliers: one query for the whole page, then
    # rank the ones that have timed orders so the fastest is obvious.
    from .services.lead_time import lead_stats_by_supplier
    lead_by_id = lead_stats_by_supplier()
    ranked = sorted((sid for sid in lead_by_id), key=lambda sid: lead_by_id[sid]['avg_days'])
    fastest_supplier_id = ranked[0] if ranked else None
    slowest_supplier_id = ranked[-1] if len(ranked) > 1 else None
    for supplier in suppliers:
        supplier.lead = lead_by_id.get(supplier.id)
        supplier.is_fastest = (supplier.id == fastest_supplier_id)
        supplier.is_slowest = (supplier.id == slowest_supplier_id)
    supplier_count = len(suppliers)
    linked_suppliers = sum(1 for supplier in suppliers if supplier.purchase_count or supplier.inbound_count)

    # 按国家分组（未填国家排在最后）
    groups_map = {}
    for supplier in suppliers:
        groups_map.setdefault((supplier.country or '').strip(), []).append(supplier)
    supplier_groups = [
        {'country': key or 'No country', 'suppliers': groups_map[key]}
        for key in sorted(groups_map, key=lambda k: (k == '', k.lower()))
    ]

    country_options = list(
        Supplier.objects.exclude(country__isnull=True).exclude(country='')
        .values_list('country', flat=True).distinct().order_by('country')
    )

    return render(request, 'stock/supplier_list.html', {
        'supplier_groups': supplier_groups,
        'query': query,
        'country_filter': country_filter,
        'country_options': country_options,
        'supplier_count': supplier_count,
        'linked_suppliers': linked_suppliers,
        'lead_tracked': len(lead_by_id),
        'can_manage': has_manager_access(request.user),
    })


@login_required
@manager_required
def supplier_detail_view(request, supplier_id):
    from .services.inbound_rollback import can_roll_back

    supplier = get_object_or_404(Supplier.objects.prefetch_related('product_types'), id=supplier_id)
    query = (request.GET.get('q') or '').strip()
    start_date = parse_date((request.GET.get('start_date') or '').strip())
    end_date = parse_date((request.GET.get('end_date') or '').strip())
    next_url = request.get_full_path()

    inbound_orders_qs = (
        InboundOrder.objects
        .filter(Q(supplier=supplier) | Q(items__supplier=supplier))
        .select_related('supplier')
        .prefetch_related('items__product', 'items__product__images', 'items__supplier')
        # Newest activity first. Ordering by invoice_date alone buried an
        # order that arrived today under one carrying a later invoice.
        .annotate(activity_at=Coalesce('received_at', 'created_at'))
        .order_by('-activity_at', '-id')
        .distinct()
    )
    direct_purchases_qs = (
        Purchase.objects
        .filter(supplier=supplier, inbound_order__isnull=True)
        .select_related('product')
        .prefetch_related('product__images')
        .order_by('-date', '-id')
    )

    if query:
        inbound_orders_qs = inbound_orders_qs.filter(
            Q(invoice_no__icontains=query) |
            Q(note__icontains=query) |
            Q(items__product__barcode__icontains=query) |
            Q(items__product__name__icontains=query) |
            Q(items__product__brand__icontains=query) |
            Q(items__product__model__icontains=query)
        ).distinct()
        direct_purchases_qs = direct_purchases_qs.filter(
            Q(product__barcode__icontains=query) |
            Q(product__name__icontains=query) |
            Q(product__brand__icontains=query) |
            Q(product__model__icontains=query)
        )

    if start_date:
        inbound_orders_qs = inbound_orders_qs.filter(created_at__date__gte=start_date)
        direct_purchases_qs = direct_purchases_qs.filter(date__date__gte=start_date)
    if end_date:
        inbound_orders_qs = inbound_orders_qs.filter(created_at__date__lte=end_date)
        direct_purchases_qs = direct_purchases_qs.filter(date__date__lte=end_date)

    history_rows = []
    history_total_amount = Decimal('0.00')
    history_total_qty = 0
    product_ids = set()
    last_purchase_at = None

    for order in inbound_orders_qs:
        if order.supplier_id == supplier.id:
            items = list(order.items.all())
        else:
            items = [item for item in order.items.all() if item.supplier_id == supplier.id]
        if not items:
            continue

        row_amount = Decimal('0.00')
        row_qty = 0
        for item in items:
            item.line_total = (item.cost_price or Decimal('0.00')) * item.quantity
            item.image_url = get_product_image_url(item.product)
            item.display_name = build_product_label(item.product)
            row_amount += item.line_total
            row_qty += item.quantity
            product_ids.add(item.product_id)

        # Sort on the day the goods actually landed; an invoice can be dated
        # a week either side of when the shipment turns up.
        #
        # The last resort is the order's own creation date, never "today":
        # falling back to today floated every order with no invoice date and
        # no receipt time straight to the top, which is how an order from May
        # came to sit above one that arrived this morning.
        arrived_on = order.received_at.date() if order.received_at else None
        created_on = order.created_at.date() if order.created_at else timezone.localdate()
        sort_at = arrived_on or order.invoice_date or created_on
        created_anchor = comparable_recorded_at(
            order.received_at or order.created_at or timezone.now())
        sort_dt = comparable_recorded_at(
            created_anchor.replace(year=sort_at.year, month=sort_at.month, day=sort_at.day)
        )
        rollback_ok, rollback_reason = can_roll_back(order)
        history_rows.append({
            'kind': 'Inbound order',
            'title': order.invoice_no or f'Inbound Order #{order.id}',
            'sort_at': sort_dt,
            'display_date': arrived_on or order.invoice_date or order.created_at.date(),
            'meta': (f'{len(items)} items'
                     + (f' | invoice {order.invoice_date:%Y-%m-%d}'
                        if order.invoice_date and arrived_on
                           and order.invoice_date != arrived_on else '')
                     + ('' if arrived_on else ' | awaiting receipt')),
            'amount': row_amount,
            'qty': row_qty,
            'items': items,
            'note': order.note or '',
            'edit_url': f"{reverse('inbound_order_edit', args=[order.id])}?{urlencode({'next': next_url})}",
            # Offered only on orders that were actually received, and only
            # while none of the stock has moved - see services/inbound_rollback.
            'rollback_url': (reverse('inbound_order_rollback', args=[order.id])
                             if order.status == 'received' else ''),
            'rollback_reason': rollback_reason,
            'can_rollback': rollback_ok,
        })
        history_total_amount += row_amount
        history_total_qty += row_qty
        if not last_purchase_at or sort_dt > comparable_recorded_at(last_purchase_at):
            last_purchase_at = sort_dt

    direct_groups = group_purchases_by_recorded_second(direct_purchases_qs)

    for group_key, items in direct_groups.items():
        group_key = comparable_recorded_at(group_key)
        row_amount = Decimal('0.00')
        row_qty = 0
        for item in items:
            item.line_total = (item.cost_price or Decimal('0.00')) * item.quantity
            item.image_url = get_product_image_url(item.product)
            item.display_name = build_product_label(item.product)
            item.edit_url = f"{reverse('direct_purchase_edit', args=[item.id])}?{urlencode({'next': next_url})}"
            row_amount += item.line_total
            row_qty += item.quantity
            product_ids.add(item.product_id)

        history_rows.append({
            'kind': 'Direct purchases',
            'title': group_key.strftime('%Y-%m-%d %H:%M:%S'),
            'sort_at': group_key,
            'display_date': group_key.date(),
            'meta': f'{len(items)} direct purchases',
            'amount': row_amount,
            'qty': row_qty,
            'items': items,
            'note': '',
        })
        history_total_amount += row_amount
        history_total_qty += row_qty
        if not last_purchase_at or group_key > comparable_recorded_at(last_purchase_at):
            last_purchase_at = group_key

    history_rows.sort(
        key=lambda row: (comparable_recorded_at(row['sort_at']), row['title']),
        reverse=True,
    )


    # ===== Scorecard (all-time, independent of the history filters above) =====
    spend_expr = ExpressionWrapper(
        F('cost_price') * F('quantity'),
        output_field=DecimalField(max_digits=14, decimal_places=2),
    )
    today_local = timezone.localdate()
    all_purchases = Purchase.objects.filter(supplier=supplier)
    agg = all_purchases.aggregate(
        spend=Sum(spend_expr),
        units=Sum('quantity'),
        skus=Count('product', distinct=True),
        orders=Count('inbound_order', distinct=True),
        last=Max('date'),
    )
    lifetime_spend = agg['spend'] or Decimal('0.00')
    year_spend = (all_purchases.filter(date__year=today_local.year)
                  .aggregate(s=Sum(spend_expr))['s'] or Decimal('0.00'))
    month_spend = (all_purchases.filter(date__year=today_local.year, date__month=today_local.month)
                   .aggregate(s=Sum(spend_expr))['s'] or Decimal('0.00'))
    direct_batches = all_purchases.filter(inbound_order__isnull=True).count()
    order_events = (agg['orders'] or 0) + direct_batches
    avg_order_value = (lifetime_spend / order_events) if order_events else Decimal('0.00')

    first_purchase = all_purchases.order_by('date').values_list('date', flat=True).first()
    orders_per_month = None
    if first_purchase and agg['last'] and order_events:
        months_active = max(1, (agg['last'].year - first_purchase.year) * 12
                            + (agg['last'].month - first_purchase.month) + 1)
        orders_per_month = order_events / months_active

    top_products = []
    for row in (all_purchases
                .values('product_id', 'product__brand', 'product__model', 'product__name', 'product__barcode')
                .annotate(spend=Sum(spend_expr), qty=Sum('quantity'))
                .order_by('-spend')[:5]):
        label = ' - '.join(p for p in [row['product__brand'], row['product__model'], row['product__name']] if p) or row['product__barcode']
        top_products.append({
            'product_id': row['product_id'],
            'label': label,
            'spend': row['spend'] or Decimal('0.00'),
            'qty': row['qty'] or 0,
        })

    # Lead time: order placed (created_at) → received (received_at). See
    # services/lead_time.py for why same-moment rows are excluded.
    from .services.lead_time import supplier_lead_stats, recent_lead_times
    lead = supplier_lead_stats(supplier)
    lead_recent = recent_lead_times(supplier)

    scorecard = {
        'lifetime_spend': lifetime_spend,
        'year_spend': year_spend,
        'month_spend': month_spend,
        'units': agg['units'] or 0,
        'skus': agg['skus'] or 0,
        'order_events': order_events,
        'inbound_orders': agg['orders'] or 0,
        'direct_batches': direct_batches,
        'avg_order_value': avg_order_value,
        'orders_per_month': orders_per_month,
        'last_purchase': agg['last'],
        'lead': lead,
        'avg_lead_days': lead['avg_days'] if lead else None,
        'lead_sample': lead['sample'] if lead else 0,
        'top_products': top_products,
    }

    history_page = Paginator(history_rows, 20).get_page(request.GET.get('page'))

    return render(request, 'stock/supplier_detail.html', {
        'supplier': supplier,
        'lead_recent': lead_recent,
        'query': query,
        'history_page': history_page,
        'history_count': len(history_rows),
        'total_amount': history_total_amount,
        'total_qty': history_total_qty,
        'product_count': len(product_ids),
        'last_purchase_at': last_purchase_at,
        'scorecard': scorecard,
        'start_date': request.GET.get('start_date', ''),
        'end_date': request.GET.get('end_date', ''),
    })


@login_required
@manager_required
def inbound_order_edit_view(request, order_id):
    order = get_object_or_404(
        InboundOrder.objects.select_related('supplier').prefetch_related('items__product', 'items__product__images'),
        id=order_id,
    )
    next_url = request.GET.get('next', '').strip() or request.POST.get('next', '').strip() or reverse('sales_records')

    if request.method == 'POST':
        form = InboundOrderEditForm(request.POST, instance=order)
        formset = InboundPurchaseFormSet(request.POST, instance=order, prefix='lines')

        if form.is_valid() and formset.is_valid():
            recorded_at = normalize_recorded_at(form.cleaned_data['recorded_at'])
            with transaction.atomic():
                order = form.save(commit=False)
                order.created_at = recorded_at
                order.save()

                for line_form in formset.forms:
                    cleaned = getattr(line_form, 'cleaned_data', None)
                    if not cleaned:
                        continue

                    purchase = line_form.instance
                    sold_units = max((purchase.quantity or 0) - (purchase.remaining or 0), 0)

                    if cleaned.get('DELETE'):
                        purchase.delete()
                        continue

                    new_quantity = cleaned['quantity']
                    purchase.quantity = new_quantity
                    purchase.remaining = new_quantity - sold_units
                    purchase.cost_price = cleaned['cost_price']
                    purchase.supplier = order.supplier
                    purchase.date = recorded_at
                    purchase.inbound_order = order
                    purchase.save()

                remaining_items = list(order.items.all())
                if not remaining_items:
                    deleted_order_id = order.id
                    order.delete()
                    transaction.on_commit(rebuild_all_daily_summaries)
                    messages.success(request, f'Order #{deleted_order_id} deleted (no products left).')
                    return redirect(next_url)

                order.total_amount = sum(
                    ((item.cost_price or Decimal('0.00')) * item.quantity for item in remaining_items),
                    Decimal('0.00'),
                )
                order.save(update_fields=['total_amount', 'supplier', 'invoice_no', 'invoice_date', 'note', 'created_at'])
                transaction.on_commit(rebuild_all_daily_summaries)

            messages.success(request, f'Order #{order.id} updated.')
            return redirect(next_url)

        messages.error(request, 'Please fix the highlighted fields.')
    else:
        form = InboundOrderEditForm(instance=order)
        formset = InboundPurchaseFormSet(instance=order, prefix='lines')

    annotate_inbound_formset_runtime(formset)
    line_summary = []
    for line_form in formset.forms:
        purchase = line_form.instance
        purchase.image_url = get_product_image_url(purchase.product)
        purchase.display_name = build_product_label(purchase.product)
        line_summary.append(purchase)

    return render(request, 'stock/inbound_order_edit.html', {
        'order': order,
        'form': form,
        'formset': formset,
        'next_url': next_url,
        'line_count': len(line_summary),
    })


@login_required
@manager_required
def direct_purchase_edit_view(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.select_related('product', 'supplier', 'inbound_order').prefetch_related('product__images'),
        id=purchase_id,
    )
    if purchase.inbound_order_id:
        next_url = request.GET.get('next', '').strip() or reverse('sales_records')
        return redirect(f"{reverse('inbound_order_edit', args=[purchase.inbound_order_id])}?{urlencode({'next': next_url})}")

    next_url = request.GET.get('next', '').strip() or request.POST.get('next', '').strip() or reverse('sales_records')
    sold_units = max((purchase.quantity or 0) - (purchase.remaining or 0), 0)

    if request.method == 'POST':
        if 'delete_purchase' in request.POST:
            if sold_units > 0:
                messages.error(request, "This purchase already has sold units and can't be deleted.")
            else:
                purchase_label = build_product_label(purchase.product)
                with transaction.atomic():
                    purchase.delete()
                    transaction.on_commit(rebuild_all_daily_summaries)
                messages.success(request, f'Purchase "{purchase_label}" deleted.')
                return redirect(next_url)

        form = DirectPurchaseEditForm(request.POST, instance=purchase)
        if form.is_valid():
            recorded_at = normalize_recorded_at(form.cleaned_data['recorded_at'])
            with transaction.atomic():
                purchase = form.save(commit=False)
                purchase.quantity = form.cleaned_data['quantity']
                purchase.remaining = purchase.quantity - sold_units
                purchase.date = recorded_at
                purchase.inbound_order = None
                purchase.save()
                transaction.on_commit(rebuild_all_daily_summaries)
            messages.success(request, f'Purchase "{build_product_label(purchase.product)}" updated.')
            return redirect(next_url)
        messages.error(request, 'Please fix the highlighted fields.')
    else:
        form = DirectPurchaseEditForm(instance=purchase)

    purchase.sold_units = sold_units
    purchase.image_url = get_product_image_url(purchase.product)
    purchase.display_name = build_product_label(purchase.product)

    return render(request, 'stock/direct_purchase_edit.html', {
        'purchase': purchase,
        'form': form,
        'next_url': next_url,
    })


@login_required
@manager_required
def supplier_create_view(request):
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save()
            messages.success(request, f'Supplier "{supplier.name}" created.')
            return _back_to(request, 'supplier_list')
    else:
        form = SupplierForm()

    return render(request, 'stock/supplier_form.html', {
        'form': form,
        'supplier': None,
        'is_create': True,
    })


@login_required
@manager_required
def supplier_edit_view(request, supplier_id):
    supplier = get_object_or_404(Supplier.objects.prefetch_related('product_types'), id=supplier_id)

    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            supplier = form.save()
            messages.success(request, f'Supplier "{supplier.name}" updated.')
            return _back_to(request, 'supplier_list')
    else:
        form = SupplierForm(instance=supplier)

    return render(request, 'stock/supplier_form.html', {
        'form': form,
        'supplier': supplier,
        'is_create': False,
    })


@login_required
@manager_required
@require_POST
def supplier_delete_view(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    supplier_name = supplier.name
    supplier.delete()
    messages.success(request, f'Supplier "{supplier_name}" deleted. Past records were kept.')
    return _back_to(request, 'supplier_list', avoid=(reverse('supplier_detail', args=[supplier_id]),))


@login_required
@admin_required
def employee_list_view(request):
    query = (request.GET.get('q') or '').strip()
    UserModel = get_user_model()
    employees_qs = (
        UserModel.objects
        .filter(is_superuser=False)
        .annotate(last_clock_in_at=Max('attendance_records__clock_in_at'))
        .order_by('-is_active', '-is_staff', 'username')
    )
    if query:
        employees_qs = employees_qs.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)
        )

    # Scope to the active store's staff (All-stores shows everyone).
    active_store, store_is_all = resolve_active_store(request)
    if not store_is_all and active_store:
        employees_qs = employees_qs.filter(store_profile__store=active_store)

    employees = list(employees_qs.select_related('store_profile__store'))
    open_shifts = {
        record.user_id: record
        for record in AttendanceRecord.objects
        .filter(user__is_superuser=False, clock_out_at__isnull=True)
        .select_related('user')
        .order_by('-clock_in_at')
    }

    for employee in employees:
        employee.role_label = 'Manager' if employee.is_staff else 'Employee'
        employee.open_shift = open_shifts.get(employee.id)

    return render(request, 'stock/employee_list.html', {
        'employees': employees,
        'query': query,
        'active_count': sum(1 for employee in employees if employee.is_active),
        'manager_count': sum(1 for employee in employees if employee.is_staff),
        'checked_in_count': len(open_shifts),
    })


@login_required
@admin_required
def employee_create_view(request):
    if request.method == 'POST':
        form = EmployeeAccountForm(request.POST)
        if form.is_valid():
            user = form.save()
            active_store, store_is_all = resolve_active_store(request)
            home_store = active_store if (not store_is_all and active_store) else Store.get_default()
            StoreProfile.objects.update_or_create(user=user, defaults={'store': home_store})
            messages.success(request, f'Employee account "{user.username}" created.')
            return redirect('employee_list')
    else:
        form = EmployeeAccountForm(initial={'is_active': True, 'role': 'employee'})

    return render(request, 'stock/employee_form.html', {
        'form': form,
        'employee': None,
        'is_create': True,
    })


@login_required
@admin_required
def employee_edit_view(request, user_id):
    UserModel = get_user_model()
    employee = get_object_or_404(UserModel, id=user_id, is_superuser=False)

    if request.method == 'POST':
        form = EmployeeAccountForm(request.POST, instance=employee)
        if form.is_valid():
            employee = form.save()
            messages.success(request, f'Employee account "{employee.username}" updated.')
            return redirect('employee_list')
    else:
        form = EmployeeAccountForm(instance=employee)

    return render(request, 'stock/employee_form.html', {
        'form': form,
        'employee': employee,
        'is_create': False,
    })


@login_required
@admin_required
@require_POST
def employee_toggle_active_view(request, user_id):
    UserModel = get_user_model()
    employee = get_object_or_404(UserModel, id=user_id, is_superuser=False)
    employee.is_active = not employee.is_active
    employee.save(update_fields=['is_active'])
    state_label = 'activated' if employee.is_active else 'paused'
    messages.success(request, f'Employee account "{employee.username}" {state_label}.')
    return redirect('employee_list')


@login_required
@admin_required
@require_POST
def employee_delete_view(request, user_id):
    UserModel = get_user_model()
    employee = get_object_or_404(UserModel, id=user_id, is_superuser=False)
    username = employee.username
    employee.delete()
    messages.success(request, f'Employee account "{username}" deleted.')
    return redirect('employee_list')


@login_required
@manager_required
def attendance_view(request):
    today = timezone.localdate()
    open_shift = (
        AttendanceRecord.objects
        .filter(user=request.user, clock_out_at__isnull=True)
        .order_by('-clock_in_at', '-id')
        .first()
    )

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        note = (request.POST.get('note') or '').strip()

        if action == 'check_in':
            if open_shift:
                messages.warning(request, 'You already have an open shift. Please check out first.')
            else:
                AttendanceRecord.objects.create(
                    user=request.user,
                    clock_in_at=timezone.now(),
                    note=append_attendance_note('', note, 'Clock-in note'),
                )
                messages.success(request, 'Check-in recorded.')
        elif action == 'check_out':
            if not open_shift:
                messages.warning(request, 'No open shift found for checkout.')
            else:
                open_shift.clock_out_at = timezone.now()
                open_shift.note = append_attendance_note(open_shift.note, note, 'Clock-out note')
                open_shift.save(update_fields=['clock_out_at', 'note'])
                messages.success(request, 'Check-out recorded.')
        return redirect('attendance')

    month_value = (request.GET.get('month') or '').strip()
    team_query = (request.GET.get('team_q') or '').strip()
    month_start, month_end = get_month_bounds(month_value)
    selected_month = month_start.strftime('%Y-%m')

    my_records = list(
        AttendanceRecord.objects
        .filter(user=request.user, clock_in_at__date__gte=month_start, clock_in_at__date__lte=month_end)
        .order_by('-clock_in_at', '-id')
    )
    my_total_duration = timedelta()
    for record in my_records:
        record.view_duration = record.worked_duration
        record.view_duration_label = format_duration_hours(record.view_duration)
        my_total_duration += record.view_duration

    context = {
        'open_shift': open_shift,
        'today': today,
        'selected_month': selected_month,
        'month_start': month_start,
        'month_end': month_end,
        'my_records': my_records,
        'my_shift_count': len(my_records),
        'my_total_hours_label': format_duration_hours(my_total_duration),
        'show_team_section': has_manager_access(request.user),
    }

    if has_manager_access(request.user):
        team_records_qs = (
            AttendanceRecord.objects
            .select_related('user')
            .filter(user__is_superuser=False, clock_in_at__date__gte=month_start, clock_in_at__date__lte=month_end)
            .order_by('-clock_in_at', '-id')
        )
        active_store, store_is_all = resolve_active_store(request)
        if not store_is_all and active_store is not None:
            team_records_qs = team_records_qs.filter(user__store_profile__store=active_store)
        if team_query:
            team_records_qs = team_records_qs.filter(
                Q(user__username__icontains=team_query) |
                Q(user__first_name__icontains=team_query) |
                Q(user__last_name__icontains=team_query)
            )

        team_records = list(team_records_qs[:120])
        team_summary = {}
        for record in team_records:
            record.view_duration = record.worked_duration
            record.view_duration_label = format_duration_hours(record.view_duration)
            summary = team_summary.setdefault(record.user_id, {
                'user': record.user,
                'shift_count': 0,
                'total_duration': timedelta(),
                'open_shift': False,
                'last_clock_in_at': record.clock_in_at,
            })
            summary['shift_count'] += 1
            summary['total_duration'] += record.view_duration
            if record.is_open:
                summary['open_shift'] = True
            if record.clock_in_at > summary['last_clock_in_at']:
                summary['last_clock_in_at'] = record.clock_in_at

        team_members = []
        for summary in team_summary.values():
            summary['total_hours_label'] = format_duration_hours(summary['total_duration'])
            team_members.append(summary)
        team_members.sort(key=lambda item: (not item['open_shift'], item['user'].username.lower()))

        context.update({
            'team_query': team_query,
            'team_records': team_records,
            'team_members': team_members,
            'team_open_shift_count': sum(1 for item in team_members if item['open_shift']),
        })

    return render(request, 'stock/attendance.html', context)



# -----------------------------
# 导出（任意时间范围的销售 + 采购）
# -----------------------------
@login_required
@manager_required
def export_sales_purchases_pdf(request):
    """Orders > Export PDF. Built from the Orders page's own rows
    (orders_views.export_report) so the paper matches the screen."""
    from .orders_views import export_report
    return export_report(request)


@login_required
def set_active_store(request):
    """Managers/admins switch the active store (or 'All stores'). Employees are
    locked to their home store and this is a no-op for them."""
    if request.method == 'POST' and can_switch_store(request.user):
        value = (request.POST.get('store') or '').strip()
        if value == ALL_STORES:
            request.session[ACTIVE_STORE_SESSION_KEY] = ALL_STORES
        elif value.isdigit() and Store.objects.filter(id=int(value), is_active=True).exists():
            request.session[ACTIVE_STORE_SESSION_KEY] = int(value)
    return redirect(request.META.get('HTTP_REFERER') or 'dashboard')


@login_required
@admin_required
def store_list_view(request):
    stores = (
        Store.objects
        .annotate(
            staff_count=Count('staff', distinct=True),
            order_count=Count('sale_orders', distinct=True),
        )
        .order_by('-is_default', 'name')
    )
    return render(request, 'stock/store_list.html', {
        'stores': stores,
        'store_count': stores.count(),
    })


def _store_form_view(request, store):
    if request.method == 'POST':
        form = StoreForm(request.POST, instance=store)
        if form.is_valid():
            saved = form.save()
            # Keep exactly one default store at all times.
            if saved.is_default:
                Store.objects.exclude(pk=saved.pk).update(is_default=False)
            if not Store.objects.filter(is_default=True).exists():
                Store.objects.filter(pk=saved.pk).update(is_default=True)
            messages.success(request, f'Store "{saved.name}" saved.')
            return redirect('store_list')
    else:
        form = StoreForm(instance=store)

    return render(request, 'stock/store_form.html', {
        'form': form,
        'store': store,
        'is_create': store is None,
    })


@login_required
@admin_required
def store_create_view(request):
    return _store_form_view(request, None)


@login_required
@admin_required
def store_edit_view(request, store_id):
    store = get_object_or_404(Store, id=store_id)
    return _store_form_view(request, store)


@login_required
@admin_required
@require_POST
def store_delete_view(request, store_id):
    store = get_object_or_404(Store, id=store_id)
    if store.is_default:
        messages.error(request, 'The default store cannot be deleted. Set another store as default first.')
    elif store.sale_orders.exists() or store.sales.exists() or store.staff.exists() or store.ar_invoices.exists():
        messages.error(request, 'This store has sales, invoices or staff. Deactivate it instead of deleting.')
    else:
        name = store.name
        store.delete()
        messages.success(request, f'Store "{name}" deleted.')
    return redirect('store_list')


@login_required
@manager_required
def daily_summary_view(request):
    """A focused, store-scoped end-of-day summary: today's orders, KPIs, payment
    mix, top products and stock received. Defaults to today; ?date= views a day."""
    show_profit = bool(getattr(request.user, 'is_superuser', False))
    show_order_financials = has_order_reconciliation_access(request.user)
    active_store, store_is_all = resolve_active_store(request)
    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    today = timezone.localdate()
    day = parse_date((request.GET.get('date') or '').strip()) or today
    if day > today:
        day = today

    orders_qs = scope_sales_by_store(
        SaleOrder.objects
        .filter(created_at__date=day)
        .select_related('customer', 'store')
        .prefetch_related('items__product', 'items__product__images', 'payments', 'charges'),
        active_store, store_is_all,
    ).order_by('-created_at', '-id')  # most recent sale first

    profit_map = {}
    if show_profit:
        sale_ids_qs = scope_sales_by_store(
            Sale.objects.filter(order__created_at__date=day), active_store, store_is_all,
        )
        profit_map = sale_profit_map_for_sale_ids(sale_ids_qs.values_list('id', flat=True))

    total_amount = Decimal('0.00')
    total_qty = 0
    total_profit = Decimal('0.00')
    pay_totals = defaultdict(Decimal)
    product_stats = {}
    orders = []
    # Shipping and fees: in the money taken (the payment mix), not in sales.
    total_charges = Decimal('0.00')

    for order in orders_qs:
        items = list(order.items.all())
        order_total = Decimal('0.00')
        order_qty = 0
        order_profit = Decimal('0.00')

        for item in items:
            item.display_name = build_product_label(item.product)
            item.image_url = get_product_image_url(item.product)
            item.line_total = (item.unit_price or Decimal('0.00')) * item.quantity
            item.payment_label = payment_labels.get(item.payment_method, (item.payment_method or 'Other').title())
            item.line_profit = profit_map.get(item.id, {}).get('profit', Decimal('0.00')) if show_profit else Decimal('0.00')
            order_total += item.line_total
            order_qty += item.quantity
            order_profit += item.line_profit

            stats = product_stats.setdefault(item.product_id, {
                'product': item.product, 'name': item.display_name,
                'qty': 0, 'revenue': Decimal('0.00'),
            })
            stats['qty'] += item.quantity
            stats['revenue'] += item.line_total

        # Payment mix from the authoritative order-level tender (SaleOrderPayment)
        # so split payments (card + cash) are shown, not just the primary method.
        order_pay = order_tender_amounts(order, order_total, items)
        order.summary_charges = sum((c.amount for c in order.charges.all()), Decimal('0.00'))
        total_charges += order.summary_charges

        order.summary_total = order_total
        order.summary_qty = order_qty
        order.summary_profit = order_profit
        order.summary_customer = order.customer.name if order.customer_id else 'Walk-in'
        order.summary_store = order.store.name if order.store_id else '—'
        order.summary_time = timezone.localtime(order.created_at).strftime('%H:%M')
        order.summary_pay = [
            {'label': payment_labels.get(m, (m or 'Other').title()), 'amount': a}
            for m, a in sorted(order_pay.items(), key=lambda kv: (-kv[1], kv[0] or '')) if m
        ]
        order.summary_items = items
        order.detail_url = reverse('sale_order_detail', args=[order.id])
        orders.append(order)

        total_amount += order_total
        total_qty += order_qty
        total_profit += order_profit
        for method, amount in order_pay.items():
            if method:
                pay_totals[method] += amount

    order_count = len(orders)
    avg_order = (total_amount / order_count) if order_count else Decimal('0.00')

    pay_total_sum = sum(pay_totals.values())
    payment_rows = [
        {
            'label': payment_labels.get(method, (method or 'Other').title()),
            'amount': amount,
            'pct': round(float(amount) / float(pay_total_sum) * 100, 1) if pay_total_sum else 0,
        }
        for method, amount in sorted(pay_totals.items(), key=lambda kv: (-kv[1], kv[0] or ''))
    ]
    payment_chart = [{'label': row['label'], 'amount': float(row['amount'])} for row in payment_rows]

    stats_list = sorted(product_stats.values(), key=lambda s: (s['revenue'], s['qty']), reverse=True)
    max_revenue = max((s['revenue'] for s in stats_list), default=Decimal('0.00'))
    top_products = [
        {
            'product': s['product'], 'name': s['name'], 'qty': s['qty'], 'revenue': s['revenue'],
            'bar': round(float(s['revenue']) / float(max_revenue) * 100, 1) if max_revenue else 0,
        }
        for s in stats_list[:8]
    ]

    # Stock received that day (inventory is shared across stores).
    received_units = 0
    received_cost = Decimal('0.00')
    received_orders = 0
    for inbound in InboundOrder.objects.filter(created_at__date=day).prefetch_related('items'):
        received_orders += 1
        for item in inbound.items.all():
            received_units += item.quantity
            received_cost += (item.cost_price or Decimal('0.00')) * item.quantity
    for purchase in Purchase.objects.filter(inbound_order__isnull=True, date__date=day):
        received_units += purchase.quantity
        received_cost += (purchase.cost_price or Decimal('0.00')) * purchase.quantity

    next_day = day + timedelta(days=1)
    return render(request, 'stock/daily_summary.html', {
        'day': day,
        'is_today': day == today,
        'prev_day_url': f"{reverse('daily_summary')}?date={(day - timedelta(days=1)).isoformat()}",
        'next_day_url': f"{reverse('daily_summary')}?date={next_day.isoformat()}" if next_day <= today else '',
        'today_url': reverse('daily_summary'),
        'full_records_url': f"{reverse('sales_records')}?start_date={day.isoformat()}&end_date={day.isoformat()}",
        'orders': orders,
        'order_count': order_count,
        'total_amount': total_amount,
        'total_charges': total_charges,
        'total_qty': total_qty,
        'total_profit': total_profit,
        'avg_order': avg_order,
        'payment_rows': payment_rows,
        'payment_chart': payment_chart,
        'top_products': top_products,
        'received_orders': received_orders,
        'received_units': received_units,
        'received_cost': received_cost,
        'show_profit': show_profit,
        'show_order_financials': show_order_financials,
        'store_is_all': store_is_all,
    })


def _employee_sales_day_view(request):
    """Employee Sales: a single day's orders, amounts only, no charts."""
    day = timezone.localdate()
    day_str = (request.GET.get('date') or '').strip()
    if day_str:
        try:
            day = datetime.strptime(day_str, '%Y-%m-%d').date()
        except ValueError:
            day = timezone.localdate()

    active_store, store_is_all = resolve_active_store(request)

    order_str = (request.GET.get('order') or '').strip()
    if order_str:
        oq = SaleOrder.objects.filter(id=order_str) if order_str.isdigit() else SaleOrder.objects.none()
        found = scope_sales_by_store(oq, active_store, store_is_all).first()
        if found:
            return redirect('sale_order_detail', order_id=found.id)
        messages.warning(request, f'Order #{order_str} not found.')

    orders_qs = (
        SaleOrder.objects
        .filter(created_at__date=day)
        .select_related('customer')
        .prefetch_related('items', 'payments')
        .order_by('-created_at', '-id')
    )
    # Reuse the shared store scoping (unfiltered when store is None / "all stores").
    orders_qs = scope_sales_by_store(orders_qs, active_store, store_is_all)

    payment_labels = {'cash': 'Cash', 'card': 'Card', 'mbway': 'MBWay'}
    orders = []
    day_total = Decimal('0.00')
    for order in orders_qs:
        items = list(order.items.all())
        total = sum((i.quantity * (i.unit_price or Decimal('0.00')) for i in items), Decimal('0.00'))
        qty = sum(i.quantity for i in items)
        methods = [payment_labels.get(p.method, (p.method or '').title()) for p in order.payments.all()]
        orders.append({
            'order_id': order.id,
            'created_hhmm': timezone.localtime(order.created_at).strftime('%H:%M'),
            'customer_name': order.customer.name if order.customer_id else 'Walk-in / No customer',
            'item_count': qty,
            'payment_label': ', '.join(dict.fromkeys(methods)) or '-',
            'total_amount': total,
            'items': [
                {
                    'name': build_product_label(i.product),
                    'qty': i.quantity,
                    'unit_price': i.unit_price or Decimal('0.00'),
                    'line_total': (i.unit_price or Decimal('0.00')) * i.quantity,
                }
                for i in items
            ],
        })
        day_total += total

    return render(request, 'stock/sales_records_employee.html', {
        'day': day,
        'date_value': day.strftime('%Y-%m-%d'),
        'orders': orders,
        'order_count': len(orders),
        'day_total': day_total,
    })


@login_required
def record_view(request):
    """Orders. Employees get their single-day page; managers get Sales /
    Purchases / Analysis over one date range (stock/orders_views.py)."""
    if not has_manager_access(request.user):
        return _employee_sales_day_view(request)
    from .orders_views import orders_page
    return orders_page(request)


# -----------------------------
# 其他：图片删除、客户相关、自动完成、目录页
# -----------------------------
@require_POST
@login_required
@manager_required
def delete_product_image(request, image_id):
    image = get_object_or_404(ProductImage, id=image_id)
    product_id = image.product.id
    image.delete()

    # 若是 AJAX 请求，返回 JSON；否则跳转回编辑页
    if request.headers.get('x-requested-with') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    return redirect('edit_product', pk=product_id)


@login_required
def check_customer(request):
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'exists': False})

    customer = Customer.objects.filter(
        Q(nif__icontains=query) | Q(name__icontains=query)
    ).first()

    if customer:
        return JsonResponse({
            'exists': True,
            'id': customer.id,
            'name': customer.name,
            'phone': customer.phone,
            'email': customer.email,
        })

    return JsonResponse({'exists': False})


@login_required
@manager_required
def alerts_low_stock(request):
    """The alerts panel: every product running out, as an HTML fragment.

    Fetched by base.html after a page loads, so no page waits for it; the data
    is cached for a few minutes (services/low_stock.py).
    """
    return render(request, 'stock/_low_stock_panel.html', {'low': low_stock_data()})


@login_required
def customers_autocomplete(request):
    query = request.GET.get('q', '').strip()
    results = []

    if query:
        # Phone is searchable because most customers here never gave a usable
        # NIF, so it is often the only thing that identifies them.
        customers = Customer.objects.filter(
            Q(name__icontains=query) | Q(nif__icontains=query)
            | Q(phone__icontains=query)
        )
        # ?kind=revenda: Pedido quotes resellers only.
        kind = (request.GET.get('kind') or '').strip().lower()
        if kind in (Customer.RETAIL, Customer.REVENDA):
            customers = customers.filter(kind=kind)
        customers = customers.order_by('name')[:10]

        # Extra keys are additive - the till reads name and id and ignores the
        # rest; the quote screen shows the phone and address it will print.
        results = [{'id': c.id, 'name': c.name, 'nif': c.nif or '', 'label': str(c),
                    'phone': c.phone or '', 'address': c.address or '',
                    'kind': c.kind} for c in customers]

    return JsonResponse({'results': results})


@login_required
def quick_import(request):
    """Quick import for Inbound, Sell and Pedido: read pasted text or a PDF.

    POST ``text`` or ``file`` (+ ``mode``) -> the lines read, each with the
    product it matched (by EAN, else by name) and up to three guesses.
    GET ``?product=<id>`` -> one product in the same shape, for a line the
    person matched by hand. Nothing is saved here: the page shows the lines
    for confirmation, then fills its own form.
    """
    from .services import quick_import as qi
    mode = (request.POST.get('mode') or request.GET.get('mode') or 'sell').strip()
    if mode not in ('inbound', 'sell', 'pedido'):
        mode = 'sell'
    if mode != 'sell' and not has_manager_access(request.user):
        return JsonResponse({'error': 'Not allowed.'}, status=403)

    if request.method == 'GET':
        raw = (request.GET.get('product') or '').strip()
        product = Product.objects.filter(id=int(raw)).first() if raw.isdigit() else None
        if product is None:
            return JsonResponse({'error': 'Product not found.'}, status=404)
        return JsonResponse({'product': _quick_import_product(product)})

    if request.method != 'POST':
        return JsonResponse({'error': 'POST text or a PDF.'}, status=405)
    try:
        upload = request.FILES.get('file')
        if upload is not None:
            if not (upload.name or '').lower().endswith('.pdf'):
                raise qi.QuickImportError('Only PDF files can be read - or paste the text.')
            text = qi.pdf_text(upload)
        else:
            text = request.POST.get('text') or ''
        if not text.strip():
            raise qi.QuickImportError('Paste the lines or choose a PDF first.')
        rows = qi.match_products(qi.parse_text(text))
    except qi.QuickImportError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    if not rows:
        return JsonResponse({'error': 'No product lines found in this text.'}, status=400)

    def money(v):
        return f'{v:.2f}' if v is not None else None

    return JsonResponse({'rows': [{
        'ean': row['ean'] or '',
        'name': row['name'],
        'qty': row['qty'],
        'qty_read': row['qty_read'],
        'unit_price': money(row['unit_price']),
        'total': money(row['total']),
        'check': row['check'],
        'match': row['match'],
        'product': _quick_import_product(row['product']) if row['product'] else None,
        'candidates': [{'id': p.id, 'title': p.display_name, 'barcode': p.barcode}
                       for p in row['candidates']],
    } for row in rows]})


def _quick_import_product(product):
    """Everything any of the three pages needs to add the product as a line."""
    from .pedido_views import _availability_label, _on_order_ids
    ctx = _sell_line_ctx(product)
    ctx.update({
        'id': product.id,
        'display_name': product.display_name,
        'wholesale_price': ctx['wholesale'],
        'availability': _availability_label(product, _on_order_ids()),
    })
    return ctx


@login_required
def customer_last_prices(request):
    """What a customer last paid for each product they have bought.

    Sell and Pedido load this once a customer is picked, and a product added
    after that starts at this price instead of retail or wholesale. Keyed by
    product id, with the barcode alongside because Sell works by barcode.
    """
    from .services.order_charges import last_prices
    raw = (request.GET.get('customer') or '').strip()
    if not raw.isdigit():
        return JsonResponse({'results': []})
    found = last_prices(int(raw))
    barcodes = dict(Product.all_objects.filter(id__in=found.keys())
                    .values_list('id', 'barcode'))
    results = [{'product_id': product_id,
                'barcode': barcodes.get(product_id) or '',
                'price': f"{info['price']:.2f}",
                'date': timezone.localtime(info['date']).strftime('%d/%m/%Y') if info['date'] else ''}
               for product_id, info in found.items()]
    return JsonResponse({'results': results})


@require_POST
@login_required
def add_customer(request):
    nif = (request.POST.get('nif') or '').strip()
    name = (request.POST.get('name') or '').strip()
    phone = (request.POST.get('phone') or '').strip() or None
    email = (request.POST.get('email') or '').strip() or None
    notes = (request.POST.get('notes') or '').strip() or None
    address = (request.POST.get('address') or '').strip()
    # Anything but a known kind falls back to Retail: this endpoint is posted to
    # from the till as well, where no kind is sent at all.
    kind = (request.POST.get('kind') or '').strip().lower()
    if kind not in (Customer.RETAIL, Customer.REVENDA):
        kind = Customer.RETAIL

    # Name and phone are required; NIF is optional since 2026-10-25 (most
    # customers never give a usable one). The phone is what tells customers
    # apart now, so the same number twice is refused.
    from .services.contacts import customer_with_phone
    if not name:
        return JsonResponse({'success': False, 'error': 'Name is required.'})
    if not phone:
        return JsonResponse({'success': False, 'error': 'Phone is required.'})
    taken = customer_with_phone(phone)
    if taken:
        return JsonResponse({'success': False,
                             'error': f'This phone already belongs to {taken.name}.'})
    if nif and (not nif.isdigit() or len(nif) != 9):
        return JsonResponse({'success': False, 'error': 'NIF must be exactly 9 digits.'})
    if nif and Customer.objects.filter(nif=nif).exists():
        return JsonResponse({'success': False, 'error': 'Customer with this NIF already exists.'})

    customer = Customer.objects.create(
        nif=nif or None, name=name, kind=kind, phone=phone, email=email,
        address=address, notes=notes
    )

    return JsonResponse({
        'success': True,
        'id': customer.id,
        'name': customer.name,
        'nif': customer.nif or '',
        'label': str(customer),
        'phone': customer.phone,
        'email': customer.email,
        'address': customer.address,
        'kind': customer.kind,
    })

def _employee_customer_search_view(request):
    """Employee Customers: search + add only; no full list, no history."""
    query = (request.GET.get('q') or '').strip()
    customers = []
    if query:
        customers = list(
            Customer.objects.filter(
                Q(name__icontains=query)
                | Q(phone__icontains=query)
                | Q(email__icontains=query)
                | Q(nif__icontains=query)
            ).order_by('name')[:50]
        )
    return render(request, 'stock/customer_search_employee.html', {
        'query': query,
        'customers': customers,
    })


# What the customer list can be narrowed to. Each one mirrors a label the page
# already showed, so a filter never means something different from the badge.
CUSTOMER_STATUSES = ('active', 'quiet', 'new', 'debt')

# Sortable columns, by the querystring name. A whitelist rather than taking the
# field name from the URL: an arbitrary order_by is an open door.
CUSTOMER_SORTS = {
    'last': 'last_order_at',
    'orders': 'total_orders',
    'spent': 'total_spent',
    'balance': 'balance_due',
    'name': 'name',
}
# Money columns. Today nobody without the permission reaches this view at all
# (employees are handed a different one), so these guards never fire - they are
# here to keep the sort options and the columns in step if
# has_sales_sensitive_access ever stops being an alias for manager access,
# which is the only reason it is a separate function.
CUSTOMER_SENSITIVE_SORTS = {'spent', 'balance'}


AVATAR_TINTS = 6


def customer_initials(name):
    """First and last initial - "Kapaika Perfumes" reads as KP.

    One word gives its first two letters, because a single initial in a circle
    is hard to tell apart in a list of forty.
    """
    parts = [part for part in (name or '').split() if part]
    if not parts:
        return '?'
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def customer_avatar_tint(name):
    """A colour index that never changes for a given name."""
    return sum(ord(char) for char in (name or '')) % AVATAR_TINTS


def _customer_status_q(status, cutoff):
    """The same three activity states the rows show, plus open balance."""
    if status == 'active':
        return Q(last_order_at__gte=cutoff)
    if status == 'quiet':
        return (Q(total_orders__gt=0)
                & (Q(last_order_at__lt=cutoff) | Q(last_order_at__isnull=True)))
    if status == 'new':
        return Q(total_orders=0)
    if status == 'debt':
        return Q(balance_due__gt=0)
    return Q()


def _querystring(**params):
    """``a=1&b=2&`` - trailing separator included so a template can append.

    Blank values are dropped, so a link carries only the filters actually in
    force and the URL stays readable.
    """
    pairs = [f'{key}={quote_plus(str(value))}'
             for key, value in params.items() if value not in (None, '')]
    return ('&'.join(pairs) + '&') if pairs else ''


@login_required
def customer_search_view(request):
    if not has_manager_access(request.user):
        return _employee_customer_search_view(request)
    query = request.GET.get('q', '').strip()
    show_sensitive = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)

    kind_filter = (request.GET.get('kind') or '').strip().lower()
    if kind_filter not in (Customer.RETAIL, Customer.REVENDA):
        kind_filter = ''

    status_filter = (request.GET.get('status') or '').strip().lower()
    if status_filter not in CUSTOMER_STATUSES:
        status_filter = ''
    if status_filter == 'debt' and not show_sensitive:
        status_filter = ''

    sort_key = (request.GET.get('sort') or '').strip().lower()
    if sort_key not in CUSTOMER_SORTS:
        sort_key = 'last'
    if sort_key in CUSTOMER_SENSITIVE_SORTS and not show_sales_sensitive:
        sort_key = 'last'
    # Names read naturally A-Z; everything else is a "most first" question.
    default_dir = 'asc' if sort_key == 'name' else 'desc'
    sort_dir = (request.GET.get('dir') or '').strip().lower()
    if sort_dir not in ('asc', 'desc'):
        sort_dir = default_dir

    money_field = DecimalField(max_digits=12, decimal_places=2)
    recent_cutoff = timezone.now() - timedelta(days=60)

    active_store, store_is_all = resolve_active_store(request)
    store_q = Q() if (store_is_all or active_store is None) else Q(store=active_store)

    order_count_subquery = (
        SaleOrder.objects
        .filter(store_q, customer=OuterRef('pk'))
        .values('customer')
        .annotate(total=Count('id'))
        .values('total')[:1]
    )
    spent_subquery = (
        Sale.objects
        .filter(store_q, order__customer=OuterRef('pk'))
        .values('order__customer')
        .annotate(
            total=Sum(
                ExpressionWrapper(
                    F('unit_price') * F('quantity'),
                    output_field=money_field,
                )
            )
        )
        .values('total')[:1]
    )
    balance_subquery = (
        ARInvoice.objects
        .filter(store_q, customer=OuterRef('pk'))
        .values('customer')
        .annotate(
            total=Sum(
                ExpressionWrapper(
                    F('total_amount') - F('amount_paid'),
                    output_field=money_field,
                )
            )
        )
        .values('total')[:1]
    )
    last_order_subquery = (
        SaleOrder.objects
        .filter(store_q, customer=OuterRef('pk'))
        .order_by('-created_at')
        .values('created_at')[:1]
    )

    base_qs = (
        Customer.objects
        .annotate(
            total_orders=Coalesce(Subquery(order_count_subquery, output_field=IntegerField()), Value(0)),
            total_spent=Coalesce(Subquery(spent_subquery, output_field=money_field), Value(Decimal('0.00'))),
            balance_due=Coalesce(Subquery(balance_subquery, output_field=money_field), Value(Decimal('0.00'))),
            last_order_at=Subquery(last_order_subquery, output_field=DateTimeField()),
        )
    )

    if query:
        base_qs = base_qs.filter(
            Q(nif__icontains=query)
            | Q(name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )

    # Under a specific store, show only customers who have bought there.
    if not store_is_all and active_store is not None:
        base_qs = base_qs.filter(total_orders__gt=0)

    kind_q = Q(kind=kind_filter) if kind_filter else Q()
    status_q = _customer_status_q(status_filter, recent_cutoff)

    # Each set of counts is taken before its own filter and after the other, so
    # the numbers on screen always match what clicking them would show. Counted
    # the other way round, picking "Revenda" would leave every status reading 0.
    kind_counts = base_qs.filter(status_q).aggregate(
        all_count=Count('id'),
        retail_count=Count('id', filter=Q(kind=Customer.RETAIL)),
        revenda_count=Count('id', filter=Q(kind=Customer.REVENDA)),
    )
    status_counts = base_qs.filter(kind_q).aggregate(
        all_count=Count('id'),
        active_count=Count('id', filter=_customer_status_q('active', recent_cutoff)),
        quiet_count=Count('id', filter=_customer_status_q('quiet', recent_cutoff)),
        new_count=Count('id', filter=_customer_status_q('new', recent_cutoff)),
        debt_count=Count('id', filter=_customer_status_q('debt', recent_cutoff)),
    )

    customers_qs = base_qs.filter(kind_q).filter(status_q)

    sort_field = CUSTOMER_SORTS[sort_key]
    order = F(sort_field).asc(nulls_last=True) if sort_dir == 'asc' \
        else F(sort_field).desc(nulls_last=True)
    # A second key so equal rows keep a stable order between pages.
    customers_qs = customers_qs.order_by(order, 'name', 'id')

    customer_summary = customers_qs.aggregate(
        customer_count=Count('id'),
        active_count=Count('id', filter=Q(last_order_at__gte=recent_cutoff)),
        with_orders_count=Count('id', filter=Q(total_orders__gt=0)),
        open_balance_count=Count('id', filter=Q(balance_due__gt=0)),
        open_balance_total=Coalesce(Sum('balance_due'), Value(Decimal('0.00'))),
    )
    customer_summary['new_count'] = max(
        0,
        (customer_summary['customer_count'] or 0) - (customer_summary['with_orders_count'] or 0),
    )

    # Which direction a header should ask for when clicked, and the arrow it
    # wears now. Worked out here because a template cannot flip a value without
    # a thicket of ifs, and getting it wrong makes a column un-reversible.
    sort_next, sort_arrow = {}, {}
    for key in CUSTOMER_SORTS:
        key_default = 'asc' if key == 'name' else 'desc'
        if key == sort_key:
            sort_next[key] = 'asc' if sort_dir == 'desc' else 'desc'
            sort_arrow[key] = '↑' if sort_dir == 'asc' else '↓'
        else:
            sort_next[key] = key_default
            sort_arrow[key] = ''

    paginator = Paginator(customers_qs, 40)
    page_obj = paginator.get_page(request.GET.get('page'))
    customers = []
    for customer in page_obj.object_list:
        balance_due = customer.balance_due or Decimal('0.00')
        total_orders = customer.total_orders or 0
        last_order_at = customer.last_order_at
        if balance_due > 0 and show_sensitive:
            customer.activity_label = 'Open balance'
            customer.activity_class = 'warning'
        elif total_orders == 0:
            customer.activity_label = 'New'
            customer.activity_class = 'neutral'
        elif last_order_at and last_order_at >= recent_cutoff:
            customer.activity_label = 'Active'
            customer.activity_class = 'success'
        else:
            customer.activity_label = 'Quiet'
            customer.activity_class = 'neutral'
        customer.initials = customer_initials(customer.name)
        customer.avatar_tint = customer_avatar_tint(customer.name)
        customers.append(customer)

    return render(request, 'stock/customer_search.html', {
        'query': query,
        'kind_filter': kind_filter,
        'kind_counts': kind_counts,
        'status_filter': status_filter,
        'status_counts': status_counts,
        'sort_key': sort_key,
        'sort_dir': sort_dir,
        'sort_next': sort_next,
        'sort_arrow': sort_arrow,
        'filters_on': bool(status_filter) or (sort_key, sort_dir) != ('last', 'desc'),
        # Each link changes one thing and keeps the rest, pagination included.
        'qs_keep_all': _querystring(q=query, kind=kind_filter, status=status_filter,
                                    sort=sort_key, dir=sort_dir),
        'qs_without_kind': _querystring(q=query, status=status_filter,
                                        sort=sort_key, dir=sort_dir),
        'qs_without_status': _querystring(q=query, kind=kind_filter,
                                          sort=sort_key, dir=sort_dir),
        'qs_without_sort': _querystring(q=query, kind=kind_filter,
                                        status=status_filter),
        'customers': customers,
        'page_obj': page_obj,
        'customer_summary': customer_summary,
        'show_sensitive': show_sensitive,
        'show_sales_sensitive': show_sales_sensitive,
    })

def _employee_customer_orders_view(request, customer_id):
    """Employee: a customer's orders only (for reconciliation) - no analytics."""
    customer = get_object_or_404(Customer, id=customer_id)
    active_store, store_is_all = resolve_active_store(request)
    orders_qs = (
        SaleOrder.objects.filter(customer=customer)
        .prefetch_related('items', 'payments')
        .order_by('-created_at', '-id')
    )
    orders_qs = scope_sales_by_store(orders_qs, active_store, store_is_all)
    payment_labels = {'cash': 'Cash', 'card': 'Card', 'mbway': 'MBWay'}
    orders = []
    for order in orders_qs:
        items = list(order.items.all())
        total = sum((i.quantity * (i.unit_price or Decimal('0.00')) for i in items), Decimal('0.00'))
        methods = [payment_labels.get(p.method, (p.method or '').title()) for p in order.payments.all()]
        orders.append({
            'order_id': order.id,
            'created_at': timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'),
            'item_count': sum(i.quantity for i in items),
            'payment_label': ', '.join(dict.fromkeys(methods)) or '-',
            'total_amount': total,
            'items': [
                {
                    'name': build_product_label(i.product),
                    'qty': i.quantity,
                    'unit_price': i.unit_price or Decimal('0.00'),
                    'line_total': (i.unit_price or Decimal('0.00')) * i.quantity,
                }
                for i in items
            ],
        })
    return render(request, 'stock/customer_orders_employee.html', {
        'customer': customer,
        'orders': orders,
    })


@login_required
def customer_detail_view(request, customer_id):
    if not has_manager_access(request.user):
        return _employee_customer_orders_view(request, customer_id)
    customer = get_object_or_404(Customer, id=customer_id)
    show_sensitive = has_manager_access(request.user)
    show_sales_sensitive = has_sales_sensitive_access(request.user)
    show_profit = bool(getattr(request.user, 'is_superuser', False))

    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    def build_product_label(product):
        return globals()['build_product_label'](product)

    def build_payment_breakdown(source_breakdown):
        return [
            {
                'code': method,
                'label': payment_labels.get(method, (method or 'Other').title()),
                'amount': amount,
            }
            for method, amount in sorted(source_breakdown.items(), key=lambda item: (-item[1], item[0] or ''))
            if method
        ]

    # Date-range scope (presets + custom). Default = all time.
    today = timezone.now().date()
    preset = (request.GET.get('preset') or '').strip()
    if preset == 'month':
        range_key, range_start, range_end = 'month', today.replace(day=1), today
    elif preset == 'year':
        range_key, range_start, range_end = 'year', today.replace(month=1, day=1), today
    elif preset == 'all':
        range_key, range_start, range_end = 'all', None, None
    else:
        range_start = parse_date((request.GET.get('start_date') or '').strip())
        range_end = parse_date((request.GET.get('end_date') or '').strip())
        range_key = 'custom' if (range_start or range_end) else 'all'

    if range_key == 'all':
        range_label = 'All time'
    elif range_key == 'month':
        range_label = today.strftime('%B %Y')
    elif range_key == 'year':
        range_label = str(today.year)
    else:
        range_label = f"{range_start.isoformat() if range_start else '…'} → {range_end.isoformat() if range_end else '…'}"

    active_store, store_is_all = resolve_active_store(request)
    orders_qs = scope_sales_by_store(
        SaleOrder.objects
        .filter(customer=customer)
        .prefetch_related('items__product')
        .order_by('-created_at'),
        active_store, store_is_all,
    )
    if range_start:
        orders_qs = orders_qs.filter(created_at__date__gte=range_start)
    if range_end:
        orders_qs = orders_qs.filter(created_at__date__lte=range_end)

    customer_sale_ids = list(
        Sale.objects.filter(order__in=orders_qs).values_list('id', flat=True)
    )
    sale_profit_map = sale_profit_map_for_sale_ids(customer_sale_ids) if show_profit else {}

    total_spent = Decimal('0.00')
    total_items = 0
    total_orders = 0
    total_profit = Decimal('0.00')
    largest_order_total = Decimal('0.00')
    month_map = defaultdict(lambda: {
        'days': defaultdict(lambda: {
            'orders': [],
            'totals': {'amount': Decimal('0.00'), 'qty': 0, 'profit': Decimal('0.00')},
        }),
        'totals': {'amount': Decimal('0.00'), 'qty': 0, 'profit': Decimal('0.00'), 'orders': 0},
        'payments': defaultdict(Decimal),
    })
    payment_totals = defaultdict(Decimal)
    product_stats = {}
    order_dts = []

    for order in orders_qs:
        items = list(order.items.all())
        pay_break = defaultdict(Decimal)
        order_total = Decimal('0.00')
        order_qty = 0
        order_profit = Decimal('0.00')

        for item in items:
            item.display_name = build_product_label(item.product)
            item.image_url = get_product_image_url(item.product)
            item.total_price = (item.unit_price or Decimal('0.00')) * (item.quantity or 0)
            item.payment_label = payment_labels.get(item.payment_method, (item.payment_method or 'Other').title())
            item.line_profit = sale_profit_map.get(item.id, {}).get('profit', Decimal('0.00')) if show_profit else Decimal('0.00')
            order_total += item.total_price
            order_qty += item.quantity or 0
            order_profit += item.line_profit
            pay_break[item.payment_method] += item.total_price

            stats = product_stats.get(item.product_id)
            if stats is None:
                stats = product_stats[item.product_id] = {
                    'product': item.product,
                    'name': item.display_name,
                    'image_url': item.image_url,
                    'qty': 0,
                    'spend': Decimal('0.00'),
                }
            stats['qty'] += item.quantity or 0
            stats['spend'] += item.total_price

        order.display_total_amount = order_total
        order.display_total_qty = order_qty
        order.display_total_profit = order_profit
        order.display_pay_break = build_payment_breakdown(pay_break)
        order.items_list = items
        order.detail_url = reverse('sale_order_detail', args=[order.id])
        order.detail_id = f'customer-order-{order.id}'

        total_spent += order_total
        total_items += order_qty
        total_orders += 1
        total_profit += order_profit
        order_dts.append(order.created_at)
        if order_total > largest_order_total:
            largest_order_total = order_total

        day = order.created_at.date()
        month_key = day.replace(day=1)
        month_entry = month_map[month_key]
        day_entry = month_entry['days'][day]
        day_entry['orders'].append(order)
        day_entry['totals']['amount'] += order_total
        day_entry['totals']['qty'] += order_qty
        day_entry['totals']['profit'] += order_profit
        month_entry['totals']['amount'] += order_total
        month_entry['totals']['qty'] += order_qty
        month_entry['totals']['profit'] += order_profit
        month_entry['totals']['orders'] += 1
        for method, amount in pay_break.items():
            month_entry['payments'][method] += amount
            payment_totals[method] += amount

    month_blocks = []
    for month_key in sorted(month_map.keys(), reverse=True):
        month_entry = month_map[month_key]
        day_blocks = []
        for day in sorted(month_entry['days'].keys(), reverse=True):
            day_entry = month_entry['days'][day]
            day_entry['orders'].sort(key=lambda item: item.created_at, reverse=True)
            day_blocks.append({
                'date': day,
                'orders': day_entry['orders'],
                'totals': day_entry['totals'],
                'order_count': len(day_entry['orders']),
            })

        month_blocks.append({
            'month': month_key,
            'month_label': month_key.strftime('%B %Y'),
            'day_blocks': day_blocks,
            'totals': month_entry['totals'],
            'payment_breakdown': build_payment_breakdown(month_entry['payments']),
        })

    # Monthly spend trend (oldest -> newest) for the chart.
    spend_trend = [
        {
            'label': month_key.strftime('%b %Y'),
            'amount': float(month_map[month_key]['totals']['amount']),
        }
        for month_key in sorted(month_map.keys())
    ]

    # Customer-level payment-method mix across all orders.
    pay_total_sum = sum(payment_totals.values())
    payment_mix = [
        {
            'label': payment_labels.get(method, (method or 'Other').title()),
            'amount': float(amount),
            'pct': round(float(amount) / float(pay_total_sum) * 100, 1) if pay_total_sum else 0,
        }
        for method, amount in sorted(payment_totals.items(), key=lambda kv: (-kv[1], kv[0] or ''))
        if method
    ]

    # Top products bought by this customer (by spend when allowed, else units).
    stats_list = list(product_stats.values())
    if show_sales_sensitive:
        stats_list.sort(key=lambda s: (s['spend'], s['qty']), reverse=True)
        max_metric = max((s['spend'] for s in stats_list), default=Decimal('0.00'))
    else:
        stats_list.sort(key=lambda s: (s['qty'], s['spend']), reverse=True)
        max_metric = max((s['qty'] for s in stats_list), default=0)
    top_products = []
    for stats in stats_list[:8]:
        metric = stats['spend'] if show_sales_sensitive else stats['qty']
        top_products.append({
            'product': stats['product'],
            'name': stats['name'],
            'image_url': stats['image_url'],
            'qty': stats['qty'],
            'spend': stats['spend'],
            'bar': round(float(metric) / float(max_metric) * 100, 1) if max_metric else 0,
        })

    # Purchase cadence KPIs.
    cadence = {'since': None, 'avg_gap_days': None, 'orders_per_month': None}
    if order_dts:
        first_dt = min(order_dts)
        last_dt = max(order_dts)
        cadence['since'] = first_dt
        if total_orders > 1:
            span_days = (last_dt - first_dt).days
            cadence['avg_gap_days'] = round(span_days / (total_orders - 1), 1)
            span_months = (last_dt.year - first_dt.year) * 12 + (last_dt.month - first_dt.month) + 1
            cadence['orders_per_month'] = round(total_orders / span_months, 1) if span_months else None

    today = timezone.now().date()
    ar_qs = scope_sales_by_store(
        ARInvoice.objects.filter(customer=customer).order_by('-created_at'),
        active_store, store_is_all,
    )

    if ar_qs.exists():
        agg = ar_qs.aggregate(
            total_due=Sum('total_amount'),
            total_paid=Sum('amount_paid'),
        )
        total_due = agg['total_due'] or Decimal('0.00')
        total_paid = agg['total_paid'] or Decimal('0.00')
    else:
        total_due = total_paid = Decimal('0.00')

    ar_totals = {
        'total_due': total_due,
        'total_paid': total_paid,
        'balance': total_due - total_paid,
    }

    ar_list = []
    for inv in ar_qs:
        inv.balance_val = inv.balance
        inv.is_overdue = bool(inv.due_date and inv.balance_val > 0 and inv.due_date < today)
        ar_list.append(inv)

    ar_open = [inv for inv in ar_list if inv.balance_val > 0]
    show_ar = show_sensitive and (ar_totals['balance'] > 0 and len(ar_open) > 0)

    ar_counts = {
        'unpaid': sum(1 for inv in ar_open if inv.status == 'unpaid'),
        'partial': sum(1 for inv in ar_open if inv.status == 'partial'),
        'paid': sum(1 for inv in ar_list if inv.status == 'paid'),
    }
    ar_overdue = {'count': sum(1 for inv in ar_open if inv.is_overdue)}
    ar_next_due = scope_sales_by_store(
        ARInvoice.objects
        .filter(customer=customer, status__in=['unpaid', 'partial'], due_date__isnull=False)
        .order_by('due_date'),
        active_store, store_is_all,
    ).first()
    last_order_at = orders_qs.first().created_at if total_orders else None
    average_order_value = (
        (total_spent / total_orders).quantize(Decimal('0.01'))
        if total_orders else Decimal('0.00')
    )

    return render(request, 'stock/customer_detail.html', {
        'customer': customer,
        'month_blocks': month_blocks,
        'spend_trend': spend_trend,
        'payment_mix': payment_mix,
        'top_products': top_products,
        'cadence': cadence,
        'range_key': range_key,
        'range_label': range_label,
        'range_active': bool(range_start or range_end),
        'start_date': range_start.isoformat() if range_start else '',
        'end_date': range_end.isoformat() if range_end else '',
        'total_spent': total_spent,
        'total_items': total_items,
        'total_orders': total_orders,
        'total_profit': total_profit,
        'largest_order_total': largest_order_total,
        'average_order_value': average_order_value,
        'last_order_at': last_order_at,
        'show_sensitive': show_sensitive,
        'show_sales_sensitive': show_sales_sensitive,
        'show_profit': show_profit,
        'show_ar': show_ar,
        'ar_totals': ar_totals,
        'ar_counts': ar_counts,
        'ar_overdue': ar_overdue,
        'ar_next_due': ar_next_due,
        'ar_open': ar_open,
    })


@login_required
@manager_required
def edit_customer_view(request, customer_id):
    customer = get_object_or_404(Customer, id=customer_id)

    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            return redirect('customer_detail', customer_id=customer.id)
    else:
        form = CustomerForm(instance=customer)

    return render(request, 'stock/edit_customer.html', {
        'form': form,
        'customer': customer
    })

@login_required
@manager_required
def delete_customer(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    has_sales = Sale.objects.filter(customer=customer).exists()

    if has_sales:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': 'Cannot delete: this customer has sales records.'})
        messages.error(request, 'Cannot delete: this customer has sales records.')
        return redirect('customer_detail', customer_id=pk)

    if request.method == 'POST':
        name = customer.name
        customer.delete()
        if request.headers.get('x-requested-with') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': f'Customer "{name}" deleted.'})
        messages.success(request, f'Customer "{name}" deleted.')
        return _back_to(request, 'customer_search', avoid=(reverse('customer_detail', args=[pk]),))

@login_required
def products_autocomplete(request):
    q = request.GET.get('q', '').strip()
    product_id = request.GET.get('product_id', '').strip()
    results = []
    matches = Product.objects.none()
    base_qs = _lookup_scope(
        request,
        Product.objects
        .select_related('brand_master', 'series_master')
        .prefetch_related('images'))

    # Sold out means nothing to sell, so it is noise at the till - but only at
    # the till. Inbound asks the same endpoint for the whole catalogue
    # (?scope=stock), because receiving stock is exactly what you do for a
    # product you have none of.
    selling = request.GET.get('scope') != 'stock'

    # Annotated before the branch rather than inside the search, because every
    # caller now wants to know the stock: the till filters on it, and the quote
    # screen prints the same availability wording the catalogue uses.
    stock_left = (Purchase.objects
                  .filter(product=OuterRef('pk'))
                  .values('product')
                  .annotate(total=Sum('remaining'))
                  .values('total')[:1])
    base_qs = base_qs.annotate(
        stock_left=Coalesce(Subquery(stock_left, output_field=IntegerField()),
                            Value(0))
    )

    if product_id.isdigit():
        # Asked for by id, so the caller already knows which product it means -
        # reloading a till line, or opening an order correction whose products
        # may well have sold out since. Filtering here would make those lines
        # disappear, so the stock rule belongs on the search below, not here.
        matches = base_qs.filter(id=int(product_id))[:1]
    elif q:
        if selling:
            base_qs = base_qs.filter(stock_left__gt=0)
        text_match = (
            Q(name__icontains=q) |
            Q(brand__icontains=q) |
            Q(model__icontains=q) |
            Q(spec__icontains=q) |
            Q(color__icontains=q) |
            Q(barcode__icontains=q)
        )
        # "15PM" is not in any product name - it is a handset. Accessories are
        # asked for by what they fit, so a query naming a phone also returns
        # the things made for it. Universal goods are deliberately left out:
        # every cable in the shop would otherwise answer every phone.
        device_ids = list(search_devices(q, limit=5).values_list('id', flat=True))
        if device_ids:
            text_match |= (Q(device_models__in=device_ids) |
                           Q(compatibility_groups__devices__in=device_ids))
        # Best sellers first: whoever is at the till is usually reaching for
        # the thing that sells, and a dozen results ordered by brand buries it.
        # A subquery rather than a join, so the text filter above cannot
        # multiply rows and inflate the total.
        sold_total = (Sale.objects
                      .filter(product=OuterRef('pk'))
                      .values('product')
                      .annotate(total=Sum('quantity'))
                      .values('total')[:1])
        matches = (
            base_qs
            .filter(text_match)
            .distinct()
            .annotate(sold_total=Coalesce(Subquery(sold_total,
                                                   output_field=IntegerField()),
                                          Value(0)))
            .order_by('-sold_total', 'brand', 'model', 'name', 'spec', 'color')[:12]
        )

    matches = list(matches)
    # One query for the lot rather than one per row, and only when somebody is
    # actually going to read it.
    on_order_ids = product_ids_on_order() if matches else set()
    for p in matches:
        _label, state = get_catalog_availability_parts(
            int(getattr(p, 'stock_left', 0) or 0), on_order=p.id in on_order_ids)
        results.append({
            'id': p.id,
            'name': p.name,
            'stock_left': int(getattr(p, 'stock_left', 0) or 0),
            'availability': PT_AVAILABILITY.get(state, ''),
            'display_name': p.display_name,
            'brand': p.brand or '',
            'model': p.model or '',
            'spec': p.spec or '',
            'color': p.color or '',
            'barcode': p.barcode,
            'retail_price': float(p.default_price) if p.default_price is not None else None,
            'wholesale_price': float(p.wholesale_price) if p.wholesale_price is not None else None,
            'image_url': get_product_image_url(p),
        })
    return JsonResponse({'results': results})



# -----------------------------
# 目录页（只展示有图片的产品；品牌/型号分组 + 库存&销量排序）
# -----------------------------

@login_required
@manager_required
def catalog_view(request):
    query = request.GET.get('q', '').strip()
    category_id = request.GET.get('category', '').strip()
    selected_category_name = ''

    qs = (
        annotate_catalog_metrics(
            Product.objects
            .filter(images__isnull=False)
            .distinct()
        )
        .select_related('category', 'brand_master', 'series_master')
        .prefetch_related('images')
    )

    if query:
        qs = qs.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(model__icontains=query) |
            Q(spec__icontains=query) |
            Q(color__icontains=query) |
            Q(barcode__icontains=query)
        )

    if category_id:
        qs = qs.filter(category_id=category_id)
        selected_category_name = (
            Category.objects.filter(id=category_id).values_list('name', flat=True).first() or ''
        )
        selected_category_name = customer_catalog_case(selected_category_name)

    hot_ids = set(qs.order_by('-total_sold').values_list('id', flat=True)[:5])

    from collections import defaultdict as _dd
    brand_bucket = _dd(lambda: {
        'count': 0,
        'models': _dd(lambda: {'products': [], 'model_sold': 0}),
        'outro': {'products': [], 'model_sold': 0},
    })

    all_products = []
    total_stock_units = 0
    total_sales_units = 0

    for p in qs:
        stock_val = int(p.total_stock or 0)
        p.stock_val = stock_val
        p.stock_state = 2 if stock_val >= 4 else (1 if stock_val > 0 else 0)
        p.sales_val = int(p.total_sold or 0)
        p.primary_image = p.images.first()
        title_parts = []
        model_part = (p.model or '').strip()
        name_part = (p.name or '').strip()
        if model_part:
            title_parts.append(model_part)
        if name_part and name_part.lower() != model_part.lower():
            title_parts.append(name_part)
        p.catalog_title = customer_catalog_case(' - '.join(title_parts) or name_part or p.display_name)
        p.catalog_spec = customer_catalog_case(p.spec)
        p.catalog_color = customer_catalog_case(p.color)
        p.catalog_category_name = customer_catalog_case(getattr(p.category, 'name', ''))
        if stock_val >= 4:
            p.availability_label = 'Available now'
            p.availability_class = 'in-stock'
        elif stock_val > 0:
            p.availability_label = 'Low stock'
            p.availability_class = 'low-stock'
        else:
            p.availability_label = 'Currently unavailable'
            p.availability_class = 'out-stock'
        all_products.append(p)
        total_stock_units += p.stock_val
        total_sales_units += p.sales_val

        brand = (p.brand or '').strip() or '—'
        model_key = (p.model or '').strip()

        if model_key:
            brand_bucket[brand]['models'][model_key]['products'].append(p)
            brand_bucket[brand]['models'][model_key]['model_sold'] += p.sales_val
        else:
            brand_bucket[brand]['outro']['products'].append(p)
            brand_bucket[brand]['outro']['model_sold'] += p.sales_val

        brand_bucket[brand]['count'] += 1

    def product_sort_key(prod):
        return (-prod.stock_state, -prod.stock_val, -prod.sales_val, (prod.display_name or '').lower())

    brands = []
    for brand_name, data in brand_bucket.items():
        model_blocks = []

        for model_name, mdata in sorted(
            data['models'].items(),
            key=lambda kv: (-kv[1]['model_sold'], kv[0].lower())
        ):
            prods = sorted(mdata['products'], key=product_sort_key)
            model_blocks.append({
                'model': model_name,
                'display_model': customer_catalog_case(model_name),
                'products': prods,
                'has_stock': any(prod.stock_val > 0 for prod in prods),
            })

        if data['outro']['products']:
            outro_sorted = sorted(
                data['outro']['products'],
                key=lambda p: (-p.sales_val, -p.stock_state, -p.stock_val, (p.display_name or '').lower())
            )
            model_blocks.append({
                'model': '',
                'display_model': '',
                'products': outro_sorted,
                'has_stock': any(prod.stock_val > 0 for prod in outro_sorted),
            })

        brands.append({
            'brand': brand_name,
            'display_brand': customer_catalog_case(brand_name),
            'count': data['count'],
            'models': model_blocks,
            'has_stock': any(block['has_stock'] for block in model_blocks),
        })

    brands.sort(key=lambda b: (-b['count'], b['brand'].lower()))
    categories = Category.objects.all()
    hot_products = sorted(
        [product for product in all_products if product.id in hot_ids],
        key=lambda product: (-(1 if product.stock_val > 0 else 0), -product.sales_val, -product.stock_val, (product.display_name or '').lower()),
    )

    return render(request, 'stock/catalog.html', {
        'brands': brands,
        'categories': categories,
        'query': query,
        'selected_category': category_id,
        'selected_category_name': selected_category_name,
        'hot_ids': list(hot_ids),
        'hot_products': hot_products,
        'total_catalog_products': len(all_products),
        'total_catalog_brands': len(brands),
        'total_catalog_stock': total_stock_units,
        'total_catalog_sales': total_sales_units,
        'can_manage': has_manager_access(request.user),
    })

# -----------------------------
# 导出：目录（按品牌分表）到 Excel
# -----------------------------
@login_required
@manager_required
def export_catalog_excel(request):
    q = request.GET.get('q', '').strip()
    category_id = (request.GET.get('category') or '').strip()

    qs = (
        annotate_catalog_metrics(
            Product.objects
            .filter(images__isnull=False)
            .distinct()
        )
        .select_related('category', 'brand_master', 'series_master')
        .prefetch_related('images')
    )

    if q:
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(brand__icontains=q) |
            Q(model__icontains=q) |
            Q(spec__icontains=q) |
            Q(color__icontains=q) |
            Q(barcode__icontains=q)
        )
    if category_id:
        qs = qs.filter(category_id=category_id)

    from collections import defaultdict as _dd
    brand_bucket = _dd(lambda: {
        'count': 0,
        'models': _dd(lambda: {'products': [], 'model_sold': 0}),
        'outro': {'products': [], 'model_sold': 0},
    })

    def _stock_state(v: int) -> int:
        return 2 if v > 5 else (1 if v > 0 else 0)

    for p in qs:
        p.stock_val = int(p.total_stock or 0)
        p.stock_state = _stock_state(p.stock_val)
        p.sales_val = int(getattr(p, 'total_sold', 0) or 0)

        brand = (p.brand or '').strip() or '—'
        model_key = (p.model or '').strip()
        if model_key:
            brand_bucket[brand]['models'][model_key]['products'].append(p)
            brand_bucket[brand]['models'][model_key]['model_sold'] += p.sales_val
        else:
            brand_bucket[brand]['outro']['products'].append(p)
            brand_bucket[brand]['outro']['model_sold'] += p.sales_val
        brand_bucket[brand]['count'] += 1

    if not brand_bucket:
        return HttpResponse('No products to export for current filters.', content_type='text/plain')

    wb = Workbook()
    if wb.active:
        wb.remove(wb.active)

    header_fill = PatternFill('solid', fgColor='FFE2E8F0')
    thin = Side(border_style='thin', color='FFCBD5E1')
    border = Border(top=thin, bottom=thin, left=thin, right=thin)
    wrap = Alignment(wrap_text=True, vertical='top')

    brands_sorted = sorted(
        [{'brand': b, 'count': data['count']} for b, data in brand_bucket.items()],
        key=lambda x: (-x['count'], x['brand'].lower())
    )

    def _product_sort_key(prod):
        return (-prod.stock_state, -prod.stock_val, -prod.sales_val, (prod.display_name or '').lower())

    for b in brands_sorted:
        brand_name = b['brand']
        data = brand_bucket[brand_name]

        ws = wb.create_sheet(title=(brand_name[:30].replace('/', '-') or 'Sem Marca'))
        ws.freeze_panes = 'A2'
        ws.column_dimensions['A'].width = 18
        ws.column_dimensions['B'].width = 40
        ws.column_dimensions['C'].width = 12
        ws.column_dimensions['D'].width = 16
        ws.column_dimensions['E'].width = 60

        headers = ['Foto', 'Nome', 'Preço (€)', 'Stock', 'Descrição']
        ws.append(headers)
        for col in ['A','B','C','D','E']:
            cell = ws[f"{col}1"]
            cell.fill = header_fill
            cell.font = Font(bold=True)
            cell.border = border
            cell.alignment = Alignment(vertical='center')

        model_blocks = []
        for model_name, mdata in data['models'].items():
            prods_sorted = sorted(mdata['products'], key=_product_sort_key)
            model_blocks.append({'model': model_name, 'products': prods_sorted, 'model_sold': mdata['model_sold']})
        model_blocks.sort(key=lambda kv: (-kv['model_sold'], kv['model'].lower()))

        outro_sorted = []
        if data['outro']['products']:
            outro_sorted = sorted(
                data['outro']['products'],
                key=lambda p: (-p.sales_val, -p.stock_state, -p.stock_val, (p.display_name or '').lower())
            )

        row = 2
        def _append(prod):
            nonlocal row
            # Nome
            parts = []
            if (prod.model or '').strip():
                parts.append(prod.model.strip())
            parts.append((prod.name or '').strip())
            disp = ' · '.join([x for x in parts if x]) or (prod.name or '')
            disp = prod.display_name
            ws.cell(row=row, column=2, value=disp).alignment = wrap

            # Preço
            price_val = getattr(prod, 'default_price', None)
            c_price = ws.cell(row=row, column=3, value=float(price_val) if price_val is not None else None)
            if price_val is not None:
                c_price.number_format = '€#,##0.00'

            # Stock 状态
            s_val = int(getattr(prod, 'total_stock', 0) or 0)
            if s_val > 5:
                s_text, s_color, s_font = 'Em stock', 'FFBBF7D0', Font(color='FF065F46', bold=True)
            elif s_val > 0:
                s_text, s_color, s_font = 'Baixo', 'FFFED7AA', Font(color='FF7C2D12', bold=True)
            else:
                s_text, s_color, s_font = 'Esgotado', 'FFFECACA', Font(color='FF7F1D1D', bold=True)
            c_stock = ws.cell(row=row, column=4, value=s_text)
            c_stock.fill = PatternFill('solid', fgColor=s_color)
            c_stock.font = s_font
            c_stock.border = border
            c_stock.alignment = Alignment(horizontal='center', vertical='center')

            # 描述
            desc = (prod.description or '').strip()
            if len(desc) > 240:
                desc = desc[:240] + '…'
            ws.cell(row=row, column=5, value=desc).alignment = wrap

            # 图片
            try:
                first_img = prod.images.all()[0]
                img_path = first_img.image.path
                pil = PILImage.open(img_path)
                pil.thumbnail((120, 120))
                tmp = _BytesIO(); pil.save(tmp, format='PNG'); tmp.seek(0)
                xlimg = XLImage(tmp)
                ws.add_image(xlimg, f'A{row}')
                ws.row_dimensions[row].height = 95
            except Exception:
                pass

            for c in (2,3,4,5):
                ws.cell(row=row, column=c).border = border
            row += 1

        for blk in model_blocks:
            for prod in blk['products']:
                _append(prod)
        for prod in outro_sorted:
            _append(prod)

    buf = _BytesIO(); wb.save(buf); buf.seek(0)
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    fname = f"Catalog_{ts}.xlsx"
    resp = FileResponse(buf, as_attachment=True, filename=fname)
    resp["Content-Type"] = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return resp

# -----------------------------
# 欠账
# -----------------------------
@login_required
@manager_required
def ar_new_view(request):
    # 读取 GET 里的预选客户
    pre_id = (request.GET.get('customer') or '').strip()
    pre_customer = None
    if pre_id.isdigit():
        pre_customer = Customer.objects.filter(id=int(pre_id)).only('id', 'name', 'nif').first()

    if request.method == 'POST':
        form = ARInvoiceForm(request.POST)
        if form.is_valid():
            invoice = form.save(commit=False)

            #  兜底：若表单未包含 customer 字段，则用隐藏域 customer 赋值
            if not getattr(invoice, 'customer_id', None):
                post_cust_id = (request.POST.get('customer') or '').strip()
                if post_cust_id.isdigit():
                    invoice.customer_id = int(post_cust_id)

            invoice.store = store_for_new_sale(request)
            invoice.total_amount = Decimal('0.00')
            invoice.save()

            total = Decimal('0.00')
            product_names = request.POST.getlist('product_name')
            quantities = request.POST.getlist('quantity')
            unit_prices = request.POST.getlist('unit_price')

            for name, qty, price in zip(product_names, quantities, unit_prices):
                try:
                    qty = int(qty)
                    price = Decimal(price)
                    ARItem.objects.create(
                        invoice=invoice,
                        product_name=name,
                        quantity=qty,
                        unit_price=price
                    )
                    total += qty * price
                except Exception:
                    continue

            invoice.total_amount = total
            paid = invoice.amount_paid or Decimal('0')
            invoice.status = 'paid' if paid >= total else ('partial' if paid > 0 else 'unpaid')
            invoice.save()

            messages.success(request, f"IOU #{invoice.id} created, total €{total:.2f}.")
            return redirect('ar_detail', invoice_id=invoice.id)
        else:
            messages.error(request, "Please fix the highlighted fields.")
    else:
        # GET 时把预选客户灌入初始值（如果表单包含 customer 字段会生效）
        init = {}
        if pre_customer:
            init['customer'] = pre_customer.id
        form = ARInvoiceForm(initial=init)

    return render(request, 'stock/ar_new.html', {
        'form': form,
        'pre_customer': pre_customer,  # 模板里用来预填隐藏域与搜索框
    })

@login_required
@manager_required
def ar_list_view(request):
    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    sort = request.GET.get('sort', '-id')
    show_sensitive = has_manager_access(request.user)

    active_store, store_is_all = resolve_active_store(request)
    invoices = scope_sales_by_store(ARInvoice.objects.all(), active_store, store_is_all)
    if q:
        invoices = invoices.filter(customer__name__icontains=q)
    if status:
        invoices = invoices.filter(status=status)

    invoices = invoices.order_by(sort)

    totals = invoices.aggregate(
        total_amount=Sum('total_amount') or 0,
        total_paid=Sum('amount_paid') or 0,
    )
    totals['balance'] = (totals['total_amount'] or 0) - (totals['total_paid'] or 0)

    context = {
        'q': q,
        'status': status,
        'sort': sort,
        'filters_on': sort == 'date',
        'invoices': invoices,
        'totals': totals,
        'show_sensitive': show_sensitive,
    }
    return render(request, 'stock/ar_list.html', context)

@login_required
@manager_required
def ar_detail_view(request, invoice_id):
    invoice = get_object_or_404(ARInvoice.objects.select_related('customer'), id=invoice_id)
    items = invoice.items.all().order_by('id')
    pay_form = ARPaymentForm()
    return render(request, 'stock/ar_detail.html', {
        'invoice': invoice,
        'items': items,
        'pay_form': pay_form,
        'show_sensitive': has_manager_access(request.user),
    })


@require_POST
@login_required
@manager_required
def ar_add_payment_view(request, invoice_id):
    invoice = get_object_or_404(ARInvoice, id=invoice_id)
    form = ARPaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Invalid payment.")
        return redirect('ar_detail', invoice_id=invoice.id)

    payment = form.save(commit=False)
    payment.invoice = invoice
    payment.save()

    # 汇总已付并更新状态
    total_paid = invoice.payments.aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
    invoice.amount_paid = total_paid
    if total_paid >= invoice.total_amount:
        invoice.status = 'paid'
    elif total_paid > 0:
        invoice.status = 'partial'
    else:
        invoice.status = 'unpaid'
    invoice.save()

    messages.success(request, f"Payment €{payment.amount:.2f} recorded.")
    return redirect('ar_detail', invoice_id=invoice.id)

@require_POST
@login_required
@manager_required
def ar_add_items_view(request, invoice_id):
    invoice = get_object_or_404(ARInvoice, id=invoice_id)

    names = request.POST.getlist('product_name')
    quantities = request.POST.getlist('quantity')
    prices = request.POST.getlist('unit_price')

    total_added = Decimal('0.00')

    for name, qty, price in zip(names, quantities, prices):
        if not name.strip():
            continue
        try:
            qty = int(qty)
            price = Decimal(price)
        except:
            continue
        ARItem.objects.create(
            invoice=invoice,
            product_name=name.strip(),
            quantity=qty,
            unit_price=price
        )
        total_added += qty * price

    # 更新总金额
    invoice.total_amount += total_added
    paid = invoice.amount_paid or Decimal('0')
    invoice.status = 'paid' if paid >= invoice.total_amount else ('partial' if paid > 0 else 'unpaid')
    invoice.save()

    messages.success(request, f"{len(names)} item(s) added, +€{total_added:.2f}.")
    return redirect('ar_detail', invoice_id=invoice.id)

# API: 调整单个购买批次的 remaining
@login_required
@require_POST
def api_adjust_purchase_stock(request):
    """
    POST JSON:
    {
      "purchase_id": 456,
      "new_remaining": 50
    }
    """
    if not has_manager_access(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8') if isinstance(request.body, bytes) else request.body)
        purchase_id = int(data.get('purchase_id', 0))
        new_remaining = int(data.get('new_remaining', 0))

        if new_remaining < 0:
            return JsonResponse({'success': False, 'error': 'Stock cannot be negative'})

        purchase = get_object_or_404(Purchase.objects.select_related('product'), pk=purchase_id)
        if new_remaining > purchase.quantity:
            return JsonResponse({'success': False, 'error': 'Remaining stock cannot exceed original quantity'})

        with transaction.atomic():
            old_remaining = purchase.remaining
            # 条件更新：仅当 remaining 未被并发修改时才写入（SQLite 上 select_for_update 是 no-op）
            updated = Purchase.objects.filter(pk=purchase.pk, remaining=old_remaining).update(
                remaining=new_remaining
            )
            if not updated:
                return JsonResponse({'success': False, 'error': 'Stock changed concurrently, please retry.'})

            StockAdjustmentLog.objects.create(
                user=request.user,
                product=purchase.product,
                purchase=purchase,
                adjustment_type='purchase_remaining',
                old_value=old_remaining,
                new_value=new_remaining,
            )
            inventory_snapshot = build_inventory_snapshot(purchase.product)

            from .services.pricing import sync_perfume_price
            sync_perfume_price(purchase.product)

        return JsonResponse({
            'success': True,
            'message': f'Updated remaining from {old_remaining} to {new_remaining}',
            'old_remaining': old_remaining,
            'new_remaining': new_remaining,
            'inventory_snapshot': inventory_snapshot,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


# API: 调整产品总库存（按 FIFO 创建/移除批次）
@login_required
@require_POST
def api_adjust_total_stock(request):
    """
    POST JSON:
    {
      "product_id": 271,
      "new_total_stock": 100
    }
    """
    if not has_manager_access(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied'}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8') if isinstance(request.body, bytes) else request.body)
        product_id = int(data.get('product_id', 0))
        new_total = int(data.get('new_total_stock', 0))

        if new_total < 0:
            return JsonResponse({'success': False, 'error': 'Stock cannot be negative'})

        product = get_object_or_404(Product, pk=product_id)

        with transaction.atomic():
            current_total = product.total_stock()
            difference = new_total - current_total

            if difference == 0:
                return JsonResponse({
                    'success': True,
                    'message': 'No change needed',
                    'inventory_snapshot': build_inventory_snapshot(product),
                })

            if difference > 0:
                Purchase.objects.create(
                    product=product,
                    supplier=None,
                    quantity=difference,
                    cost_price=Decimal('0.00'),
                    remaining=difference,
                    inbound_order=None
                )
                message = f'Increased stock by {difference}'
            else:
                # 复用 FIFO 原子条件扣减：SQLite 上 select_for_update 是 no-op，
                # consume_stock_fifo 用条件 UPDATE 保证并发安全
                consume_stock_fifo(product, abs(difference))
                message = f'Decreased stock by {abs(difference)}'

            from .services.pricing import sync_perfume_price
            sync_perfume_price(product)

            StockAdjustmentLog.objects.create(
                user=request.user,
                product=product,
                purchase=None,
                adjustment_type='total_stock',
                old_value=current_total,
                new_value=new_total,
            )
            inventory_snapshot = build_inventory_snapshot(product)

        return JsonResponse({
            'success': True,
            'message': message,
            'inventory_snapshot': inventory_snapshot,
        })
    except ValidationError as e:
        return JsonResponse({'success': False, 'error': '; '.join(e.messages)})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
