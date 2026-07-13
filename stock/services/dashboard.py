from calendar import month_abbr
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import ARInvoice, ARPayment, Purchase, Sale, SalesTarget
from .profit import sale_profit_map_for_sale_ids

_LINE_TOTAL_EXPR = ExpressionWrapper(
    F("unit_price") * F("quantity"),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def _apply_store(queryset, store):
    """Scope a Sale/SaleOrder/ARInvoice queryset to a store (``None`` = all stores)."""
    return queryset.filter(store=store) if store is not None else queryset


def order_tender_amounts(order, subtotal, items):
    """Per-payment-method amounts for one order, summing to ``subtotal``.

    Reads the authoritative order-level ``SaleOrderPayment`` split so in-line
    split tenders (e.g. card + cash) show up in payment statistics — not just
    the line's primary ``Sale.payment_method``. When a category filter narrows
    the order, the split is scaled to the filtered ``subtotal``. Falls back to
    each line's own method for legacy orders with no recorded tender.

    ``order.payments`` should be prefetched by the caller to avoid an N+1.
    """
    result = defaultdict(Decimal)
    payments = list(order.payments.all())
    order_total = sum((payment.amount for payment in payments), Decimal('0.00'))
    if payments and order_total > 0:
        if abs(subtotal - order_total) <= Decimal('0.01'):
            for payment in payments:
                result[payment.method] += payment.amount
        else:
            for payment in payments:
                result[payment.method] += (payment.amount / order_total) * subtotal
        return result
    for item in items:
        line_total = getattr(item, 'line_total', None)
        if line_total is None:
            line_total = (item.unit_price or Decimal('0.00')) * item.quantity
        result[item.payment_method] += line_total
    return result


def _month_start(day):
    return day.replace(day=1)


def _next_month(day):
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def _build_product_label(product):
    if not product:
        return "Unknown product"
    return product.display_name


def _get_product_image_url(product):
    if not product:
        return ""

    for image in product.images.all():
        image_file = getattr(image, "image", None)
        if not image_file:
            continue
        try:
            return image_file.url
        except ValueError:
            continue
    return ""


def resolve_dashboard_month(month_value, today):
    current_month_start = _month_start(today)

    if month_value:
        try:
            selected = datetime.strptime(month_value, "%Y-%m").date()
        except ValueError:
            selected = current_month_start
    else:
        selected = current_month_start

    selected = _month_start(selected)
    if selected > current_month_start:
        selected = current_month_start

    month_end = _next_month(selected) - timedelta(days=1)
    is_current_month = selected == current_month_start
    period_end = today if is_current_month else month_end

    return {
        "month_start": selected,
        "month_end": month_end,
        "period_end": period_end,
        "month_value": selected.strftime("%Y-%m"),
        "month_label": selected.strftime("%B %Y"),
        "scope_label": "Month to date" if is_current_month else "Full month",
        "is_current_month": is_current_month,
        "prev_month_value": (selected - timedelta(days=1)).strftime("%Y-%m"),
        "next_month_value": _next_month(selected).strftime("%Y-%m") if not is_current_month else None,
    }


def compute_period_headline(month_start, period_end, selected_category_ids=None, show_profit=False, store=None):
    """Lightweight headline totals for a period (no inventory / AR / supplier work).

    Used for the month-over-month comparison so the prior period does not pay
    for the full snapshot. Profit still requires a FIFO replay and is only
    computed when ``show_profit`` is set.
    """
    selected_category_ids = selected_category_ids or []
    sales_qs = (
        Sale.objects
        .annotate(business_at=Coalesce("order__created_at", "date"))
        .filter(business_at__date__gte=month_start, business_at__date__lte=period_end)
    )
    sales_qs = _apply_store(sales_qs, store)
    if selected_category_ids:
        sales_qs = sales_qs.filter(product__category_id__in=selected_category_ids)

    agg = sales_qs.aggregate(amount=Sum(_LINE_TOTAL_EXPR), qty=Sum("quantity"))
    sales_amount = agg["amount"] or Decimal("0.00")
    sales_qty = int(agg["qty"] or 0)

    non_null_orders = sales_qs.filter(order__isnull=False).values("order_id").distinct().count()
    null_order_sales = sales_qs.filter(order__isnull=True).count()
    order_count = non_null_orders + null_order_sales
    avg_ticket = (sales_amount / order_count) if order_count else Decimal("0.00")

    profit = Decimal("0.00")
    if show_profit:
        sale_ids = list(sales_qs.values_list("id", flat=True))
        profit_map = sale_profit_map_for_sale_ids(sale_ids) if sale_ids else {}
        profit = sum(
            (profit_map.get(sale_id, {}).get("profit", Decimal("0.00")) for sale_id in sale_ids),
            Decimal("0.00"),
        )

    return {
        "sales_amount": sales_amount,
        "sales_qty": sales_qty,
        "order_count": order_count,
        "avg_ticket": avg_ticket,
        "profit": profit,
    }


def _pct_delta(current, previous):
    """Signed percent change of ``current`` vs ``previous``; None if no baseline."""
    previous = Decimal(previous or 0)
    if previous == 0:
        return None
    return float((Decimal(current or 0) - previous) / previous * Decimal("100"))


def build_period_comparison(month_context, current_headline, selected_category_ids=None, show_profit=False, store=None):
    """Compare the current period against the same-length window of the prior month."""
    month_start = month_context["month_start"]
    period_end = month_context["period_end"]
    days_elapsed = (period_end - month_start).days + 1

    prev_month_last_day = month_start - timedelta(days=1)
    prev_month_start = _month_start(prev_month_last_day)
    prev_period_end = min(prev_month_start + timedelta(days=days_elapsed - 1), prev_month_last_day)

    prior = compute_period_headline(prev_month_start, prev_period_end, selected_category_ids, show_profit, store)

    return {
        "prev_month_label": prev_month_start.strftime("%B %Y"),
        "window_days": days_elapsed,
        "prev_sales_amount": prior["sales_amount"],
        "prev_profit": prior["profit"],
        "prev_order_count": prior["order_count"],
        "prev_avg_ticket": prior["avg_ticket"],
        "sales_amount_delta_pct": _pct_delta(current_headline["sales_amount"], prior["sales_amount"]),
        "profit_delta_pct": _pct_delta(current_headline["profit"], prior["profit"]) if show_profit else None,
        "order_count_delta_pct": _pct_delta(current_headline["order_count"], prior["order_count"]),
        "avg_ticket_delta_pct": _pct_delta(current_headline["avg_ticket"], prior["avg_ticket"]),
    }


def build_target_progress(month_context, current_sales_amount, selected_category_ids=None):
    """Monthly revenue-target progress + run-rate projection for the current month.

    Sums the per-category :class:`SalesTarget` rows matching the dashboard's
    category filter (all defined targets when no filter is active). Returns
    ``None`` when no positive target applies.
    """
    targets = SalesTarget.objects.all()
    if selected_category_ids:
        targets = targets.filter(category_id__in=selected_category_ids)
    target_amount = targets.aggregate(total=Sum("monthly_amount"))["total"] or Decimal("0.00")
    if target_amount <= 0:
        return None

    current_sales_amount = current_sales_amount or Decimal("0.00")
    month_start = month_context["month_start"]
    period_end = month_context["period_end"]
    days_in_month = (_next_month(month_start) - month_start).days
    days_elapsed = (period_end - month_start).days + 1
    is_current_month = month_context.get("is_current_month", False)

    progress_pct = float(current_sales_amount / target_amount * Decimal("100"))
    projected_amount = None
    projected_pct = None
    if is_current_month and days_elapsed > 0:
        projected_amount = current_sales_amount / Decimal(days_elapsed) * Decimal(days_in_month)
        projected_pct = float(projected_amount / target_amount * Decimal("100"))

    return {
        "target_amount": target_amount,
        "current_amount": current_sales_amount,
        "progress_pct": progress_pct,
        "projected_amount": projected_amount,
        "projected_pct": projected_pct,
        "is_current_month": is_current_month,
        "days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
    }


def resolve_year(year_value, today):
    """Clamp a requested year to [first sale year .. current year]."""
    current_year = today.year
    try:
        year = int(year_value)
    except (TypeError, ValueError):
        year = current_year
    if year > current_year:
        year = current_year
    return year


def build_yearly_sales_overview(year, today, selected_category_ids=None, show_profit=False, store=None):
    """Full-year sales overview: 12-month chart + per-month breakdown + payment mix.

    Returns monthly rows (sales / qty / orders / profit), year totals, a payment
    breakdown, and chart series for the 12 calendar months. Profit requires a
    single FIFO replay over the year's sales and is only computed when
    ``show_profit`` is set.
    """
    selected_category_ids = selected_category_ids or []
    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    period_end = min(year_end, today) if year >= today.year else year_end

    sales = list(
        _apply_store(
            Sale.objects
            .annotate(business_at=Coalesce("order__created_at", "date"))
            .filter(business_at__date__gte=year_start, business_at__date__lte=period_end),
            store,
        )
        .filter(**({"product__category_id__in": selected_category_ids} if selected_category_ids else {}))
        .values("id", "unit_price", "quantity", "payment_method", "order_id", "business_at")
    )

    sale_ids = [row["id"] for row in sales]
    profit_map = sale_profit_map_for_sale_ids(sale_ids) if show_profit and sale_ids else {}

    months = {
        m: {"amount": Decimal("0.00"), "qty": 0, "profit": Decimal("0.00"), "order_keys": set()}
        for m in range(1, 13)
    }
    payment_totals = defaultdict(Decimal)
    year_amount = Decimal("0.00")
    year_qty = 0
    year_profit = Decimal("0.00")
    year_order_keys = set()

    for row in sales:
        month = timezone.localtime(row["business_at"]).month
        line_total = (row["unit_price"] or Decimal("0.00")) * row["quantity"]
        line_profit = profit_map.get(row["id"], {}).get("profit", Decimal("0.00"))
        order_key = f"order-{row['order_id']}" if row["order_id"] else f"sale-{row['id']}"

        bucket = months[month]
        bucket["amount"] += line_total
        bucket["qty"] += row["quantity"]
        bucket["profit"] += line_profit
        bucket["order_keys"].add(order_key)

        payment_totals[row["payment_method"] or "other"] += line_total
        year_amount += line_total
        year_qty += row["quantity"]
        year_profit += line_profit
        year_order_keys.add(order_key)

    peak = max((b["amount"] for b in months.values()), default=Decimal("0.00"))
    monthly_rows = []
    for m in range(1, 13):
        bucket = months[m]
        order_count = len(bucket["order_keys"])
        monthly_rows.append({
            "month": m,
            "label": month_abbr[m],
            "amount": bucket["amount"],
            "qty": bucket["qty"],
            "profit": bucket["profit"],
            "order_count": order_count,
            "avg_ticket": (bucket["amount"] / order_count) if order_count else Decimal("0.00"),
            "height_pct": float((bucket["amount"] / peak * Decimal("100")) if peak else 0),
        })

    payment_breakdown = []
    for code, amount in sorted(payment_totals.items(), key=lambda kv: (-kv[1], kv[0] or "")):
        payment_breakdown.append({
            "code": code,
            "label": payment_labels.get(code, (code or "other").title()),
            "amount": amount,
            "share_pct": (amount / year_amount * Decimal("100")) if year_amount else Decimal("0.00"),
        })

    year_order_count = len(year_order_keys)
    return {
        "year": year,
        "is_current_year": year == today.year,
        "prev_year": year - 1,
        "next_year": (year + 1) if year < today.year else None,
        "monthly_rows": monthly_rows,
        "payment_breakdown": payment_breakdown,
        "year_sales_amount": year_amount,
        "year_sales_qty": year_qty,
        "year_sales_profit": year_profit,
        "year_order_count": year_order_count,
        "year_avg_ticket": (year_amount / year_order_count) if year_order_count else Decimal("0.00"),
        "best_month": max(monthly_rows, key=lambda r: r["amount"]) if year_amount else None,
    }


def build_monthly_dashboard_snapshot(month_start, period_end, selected_category_ids=None, show_profit=False, store=None):
    selected_category_ids = selected_category_ids or []
    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    sales_qs = (
        Sale.objects
        .annotate(business_at=Coalesce("order__created_at", "date"))
        .filter(business_at__date__gte=month_start, business_at__date__lte=period_end)
        .select_related("order__customer", "customer", "product", "store")
        .prefetch_related("product__images")
        .order_by("business_at", "id")
    )
    sales_qs = _apply_store(sales_qs, store)
    if selected_category_ids:
        sales_qs = sales_qs.filter(product__category_id__in=selected_category_ids)

    sales = list(sales_qs)
    sale_ids = [sale.id for sale in sales]
    sale_profit_map = sale_profit_map_for_sale_ids(sale_ids) if show_profit and sale_ids else {}

    total_sales_amount = Decimal("0.00")
    total_sales_qty = 0
    total_sales_profit = Decimal("0.00")
    payment_totals = defaultdict(Decimal)
    daily_totals = defaultdict(Decimal)
    daily_qty_totals = defaultdict(int)
    order_keys = set()
    customer_buckets = {}
    product_buckets = {}
    walk_in_amount = Decimal("0.00")
    walk_in_qty = 0
    walk_in_order_keys = set()
    store_buckets = {}

    for sale in sales:
        line_total = (sale.unit_price or Decimal("0.00")) * sale.quantity
        line_profit = sale_profit_map.get(sale.id, {}).get("profit", Decimal("0.00"))
        order = sale.order
        order_key = f"order-{order.id}" if order else f"sale-{sale.id}"
        customer = order.customer if order and order.customer_id else sale.customer
        product = sale.product

        total_sales_amount += line_total
        total_sales_qty += sale.quantity
        total_sales_profit += line_profit
        payment_totals[sale.payment_method or "other"] += line_total
        business_at = sale.business_at or sale.date
        business_day = timezone.localtime(business_at).date()
        daily_totals[business_day] += line_total
        daily_qty_totals[business_day] += sale.quantity
        order_keys.add(order_key)

        store_obj = sale.store
        store_bucket = store_buckets.setdefault(
            sale.store_id or 0,
            {
                "store_id": sale.store_id,
                "name": store_obj.name if store_obj else "Unassigned",
                "amount": Decimal("0.00"),
                "qty": 0,
                "profit": Decimal("0.00"),
                "order_keys": set(),
            },
        )
        store_bucket["amount"] += line_total
        store_bucket["qty"] += sale.quantity
        store_bucket["profit"] += line_profit
        store_bucket["order_keys"].add(order_key)

        if customer:
            customer_bucket = customer_buckets.setdefault(
                customer.id,
                {
                    "customer_id": customer.id,
                    "name": customer.name,
                    "amount": Decimal("0.00"),
                    "qty": 0,
                    "profit": Decimal("0.00"),
                    "order_keys": set(),
                },
            )
            customer_bucket["amount"] += line_total
            customer_bucket["qty"] += sale.quantity
            customer_bucket["profit"] += line_profit
            customer_bucket["order_keys"].add(order_key)
        else:
            walk_in_amount += line_total
            walk_in_qty += sale.quantity
            walk_in_order_keys.add(order_key)

        product_bucket = product_buckets.setdefault(
            product.id,
            {
                "product_id": product.id,
                "display_name": _build_product_label(product),
                "image_url": _get_product_image_url(product),
                "amount": Decimal("0.00"),
                "qty": 0,
                "profit": Decimal("0.00"),
                "order_keys": set(),
                "sale_days": set(),
                "first_sold_at": business_at,
                "last_sold_at": business_at,
            },
        )
        product_bucket["amount"] += line_total
        product_bucket["qty"] += sale.quantity
        product_bucket["profit"] += line_profit
        product_bucket["order_keys"].add(order_key)
        product_bucket["sale_days"].add(business_day)
        if business_at < product_bucket["first_sold_at"]:
            product_bucket["first_sold_at"] = business_at
        if business_at > product_bucket["last_sold_at"]:
            product_bucket["last_sold_at"] = business_at

    total_order_count = len(order_keys)
    avg_ticket = (total_sales_amount / total_order_count) if total_order_count else Decimal("0.00")
    active_customer_count = len(customer_buckets)
    sold_product_count = len(product_buckets)
    gross_margin_pct = (
        (total_sales_profit / total_sales_amount) * Decimal("100.00")
        if total_sales_amount else Decimal("0.00")
    )

    payment_breakdown = []
    for code, amount in sorted(payment_totals.items(), key=lambda item: (-item[1], item[0] or "")):
        payment_breakdown.append({
            "code": code,
            "label": payment_labels.get(code, (code or "other").title()),
            "amount": amount,
            "share_pct": (amount / total_sales_amount * Decimal("100.00")) if total_sales_amount else Decimal("0.00"),
        })

    top_customers = []
    for bucket in customer_buckets.values():
        order_count = len(bucket.pop("order_keys"))
        bucket["order_count"] = order_count
        bucket["share_pct"] = (
            (bucket["amount"] / total_sales_amount) * Decimal("100.00")
            if total_sales_amount else Decimal("0.00")
        )
        top_customers.append(bucket)
    top_customers.sort(key=lambda item: (-item["amount"], -item["order_count"], item["name"].lower()))
    top_customers = top_customers[:6]

    top_products = []
    fast_movers = []
    for bucket in product_buckets.values():
        order_count = len(bucket["order_keys"])
        sale_day_count = len(bucket["sale_days"])
        share_pct = (
            (bucket["amount"] / total_sales_amount) * Decimal("100.00")
            if total_sales_amount else Decimal("0.00")
        )
        avg_qty_per_day = (
            Decimal(bucket["qty"]) / Decimal(sale_day_count)
            if sale_day_count else Decimal("0.00")
        )
        top_products.append({
            "product_id": bucket["product_id"],
            "display_name": bucket["display_name"],
            "image_url": bucket["image_url"],
            "amount": bucket["amount"],
            "qty": bucket["qty"],
            "profit": bucket["profit"],
            "order_count": order_count,
            "share_pct": share_pct,
        })
        fast_movers.append({
            "product_id": bucket["product_id"],
            "display_name": bucket["display_name"],
            "image_url": bucket["image_url"],
            "amount": bucket["amount"],
            "qty": bucket["qty"],
            "profit": bucket["profit"],
            "order_count": order_count,
            "sale_day_count": sale_day_count,
            "avg_qty_per_day": avg_qty_per_day,
            "first_sold_at": bucket["first_sold_at"],
            "last_sold_at": bucket["last_sold_at"],
        })
    top_products.sort(key=lambda item: (-item["amount"], -item["qty"], item["display_name"].lower()))
    top_products = top_products[:6]
    fast_movers.sort(
        key=lambda item: (
            -item["qty"],
            item["sale_day_count"] or 9999,
            -item["avg_qty_per_day"],
            item["display_name"].lower(),
        )
    )
    fast_movers = fast_movers[:6]

    store_peak = max((bucket["amount"] for bucket in store_buckets.values()), default=Decimal("0.00"))
    store_breakdown = []
    for bucket in store_buckets.values():
        order_count = len(bucket["order_keys"])
        store_breakdown.append({
            "store_id": bucket["store_id"],
            "name": bucket["name"],
            "amount": bucket["amount"],
            "qty": bucket["qty"],
            "profit": bucket["profit"],
            "order_count": order_count,
            "avg_ticket": (bucket["amount"] / order_count) if order_count else Decimal("0.00"),
            "share_pct": (bucket["amount"] / total_sales_amount * Decimal("100.00")) if total_sales_amount else Decimal("0.00"),
            "height_pct": float((bucket["amount"] / store_peak * Decimal("100.00")) if store_peak else 0),
        })
    store_breakdown.sort(key=lambda item: (-item["amount"], item["name"].lower()))

    walk_in_summary = None
    if walk_in_order_keys:
        walk_in_summary = {
            "amount": walk_in_amount,
            "qty": walk_in_qty,
            "order_count": len(walk_in_order_keys),
        }

    purchases_qs = (
        Purchase.objects
        .filter(date__date__gte=month_start, date__date__lte=period_end)
        .select_related("supplier")
        .order_by("date", "id")
    )
    if selected_category_ids:
        purchases_qs = purchases_qs.filter(product__category_id__in=selected_category_ids)

    purchases = list(purchases_qs)
    month_purchase_amount = Decimal("0.00")
    month_purchase_qty = 0
    purchase_group_keys = set()
    supplier_buckets = {}

    for purchase in purchases:
        line_total = (purchase.cost_price or Decimal("0.00")) * purchase.quantity
        supplier_name = purchase.supplier.name if purchase.supplier else "No supplier linked"
        supplier_key = purchase.supplier_id or "missing"

        month_purchase_amount += line_total
        month_purchase_qty += purchase.quantity
        purchase_group_keys.add(purchase.inbound_order_id or f"purchase-{purchase.id}")

        bucket = supplier_buckets.setdefault(
            supplier_key,
            {
                "supplier_id": purchase.supplier_id,
                "name": supplier_name,
                "amount": Decimal("0.00"),
                "qty": 0,
                "lines": 0,
            },
        )
        bucket["amount"] += line_total
        bucket["qty"] += purchase.quantity
        bucket["lines"] += 1

    top_suppliers = list(supplier_buckets.values())
    top_suppliers.sort(key=lambda item: (-item["amount"], item["name"].lower()))
    top_suppliers = top_suppliers[:5]

    month_receivables_added = (
        _apply_store(ARInvoice.objects.filter(date__gte=month_start, date__lte=period_end), store)
        .aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
    )
    month_receipts_collected = (
        _apply_store(
            ARPayment.objects.filter(created_at__date__gte=month_start, created_at__date__lte=period_end),
            store,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        if store is None else
        ARPayment.objects.filter(
            created_at__date__gte=month_start, created_at__date__lte=period_end, invoice__store=store,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    open_agg = (
        _apply_store(ARInvoice.objects.filter(total_amount__gt=F("amount_paid")), store)
        .aggregate(
            balance=Sum(
                F("total_amount") - F("amount_paid"),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
            count=Count("id"),
        )
    )
    open_receivable_balance = open_agg["balance"] or Decimal("0.00")
    open_invoice_count = open_agg["count"] or 0

    overdue_invoice_count = _apply_store(ARInvoice.objects.filter(
        due_date__lt=period_end,
        total_amount__gt=F("amount_paid"),
    ), store).count()
    receivable_collection_ratio_pct = (
        (month_receipts_collected / month_receivables_added) * Decimal("100.00")
        if month_receivables_added else Decimal("0.00")
    )

    inventory_qs = (
        Purchase.objects
        .filter(remaining__gt=0)
        .select_related("product__category")
        .prefetch_related("product__images")
    )
    if selected_category_ids:
        inventory_qs = inventory_qs.filter(product__category_id__in=selected_category_ids)
    inventory_rows = list(inventory_qs)
    current_stock_units = sum((row.remaining or 0) for row in inventory_rows)
    current_stock_cost = sum(
        ((row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0))
        for row in inventory_rows
    )
    current_stock_sku_count = len({row.product_id for row in inventory_rows})

    inventory_product_buckets = {}
    for row in inventory_rows:
        bucket = inventory_product_buckets.setdefault(
            row.product_id,
            {
                "product_id": row.product_id,
                "display_name": _build_product_label(row.product),
                "image_url": _get_product_image_url(row.product),
                "stock_qty": 0,
                "stock_cost": Decimal("0.00"),
                "batch_count": 0,
            },
        )
        bucket["stock_qty"] += row.remaining or 0
        bucket["stock_cost"] += (row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0)
        bucket["batch_count"] += 1

    inventory_product_ids = list(inventory_product_buckets.keys())
    last_sale_dates = {}
    rolling_sales = {}
    period_qty_by_product = {product_id: bucket["qty"] for product_id, bucket in product_buckets.items()}
    if inventory_product_ids:
        last_sale_dates = {
            row["product_id"]: row["last_sold_at"]
            for row in (
                Sale.objects
                .annotate(business_at=Coalesce("order__created_at", "date"))
                .filter(product_id__in=inventory_product_ids)
                .values("product_id")
                .annotate(last_sold_at=Max("business_at"))
            )
        }

        rolling_window_start = period_end - timedelta(days=44)
        rolling_sales = {
            row["product_id"]: row["qty"]
            for row in (
                Sale.objects
                .annotate(business_at=Coalesce("order__created_at", "date"))
                .filter(
                    product_id__in=inventory_product_ids,
                    business_at__date__gte=rolling_window_start,
                    business_at__date__lte=period_end,
                )
                .values("product_id")
                .annotate(qty=Sum("quantity"))
            )
        }

    capital_locked_products = []
    slow_movers = []
    for bucket in inventory_product_buckets.values():
        last_sold_at = last_sale_dates.get(bucket["product_id"])
        days_since_last_sale = (
            (period_end - timezone.localtime(last_sold_at).date()).days
            if last_sold_at else None
        )
        rolling_qty = int(rolling_sales.get(bucket["product_id"]) or 0)
        period_qty = int(period_qty_by_product.get(bucket["product_id"]) or 0)
        stock_share_pct = (
            (bucket["stock_cost"] / current_stock_cost) * Decimal("100.00")
            if current_stock_cost else Decimal("0.00")
        )

        row = {
            **bucket,
            "period_sales_qty": period_qty,
            "rolling_45d_qty": rolling_qty,
            "last_sold_at": last_sold_at,
            "days_since_last_sale": days_since_last_sale,
            "stock_share_pct": stock_share_pct,
        }
        capital_locked_products.append(row)
        slow_movers.append(row)

    capital_locked_products.sort(
        key=lambda item: (-item["stock_cost"], item["rolling_45d_qty"], item["display_name"].lower())
    )
    capital_locked_products = capital_locked_products[:6]

    slow_movers.sort(
        key=lambda item: (
            item["rolling_45d_qty"],
            -(item["days_since_last_sale"] if item["days_since_last_sale"] is not None else 999999),
            -item["stock_cost"],
            item["display_name"].lower(),
        )
    )
    slow_movers = slow_movers[:6]

    perfume_inventory_rows = list(
        Purchase.objects
        .filter(remaining__gt=0, product__category__name__iexact="Perfumes")
        .select_related("product")
    )
    perfume_stock_units = sum((row.remaining or 0) for row in perfume_inventory_rows)
    perfume_stock_cost = sum(
        ((row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0))
        for row in perfume_inventory_rows
    )
    perfume_stock_sku_count = len({row.product_id for row in perfume_inventory_rows})

    best_sales_day = None
    if daily_totals:
        best_day, best_day_amount = max(daily_totals.items(), key=lambda item: (item[1], daily_qty_totals[item[0]], item[0]))
        best_sales_day = {
            "date": best_day,
            "amount": best_day_amount,
            "qty": daily_qty_totals.get(best_day, 0),
        }

    purchase_to_sales_pct = (
        (month_purchase_amount / total_sales_amount) * Decimal("100.00")
        if total_sales_amount else Decimal("0.00")
    )

    chart_data = []
    chart_peak = max(daily_totals.values(), default=Decimal("0.00"))
    cursor = month_start
    while cursor <= period_end:
        amount = daily_totals.get(cursor, Decimal("0.00"))
        chart_data.append({
            "date": cursor,
            "sales": amount,
            "height_pct": float((amount / chart_peak * Decimal("100.00")) if chart_peak else 0),
        })
        cursor += timedelta(days=1)

    return {
        "month_sales_amount": total_sales_amount,
        "month_sales_qty": total_sales_qty,
        "month_sales_profit": total_sales_profit,
        "month_sales_cost": total_sales_amount - total_sales_profit,
        "month_order_count": total_order_count,
        "month_avg_ticket": avg_ticket,
        "month_active_customer_count": active_customer_count,
        "month_sold_product_count": sold_product_count,
        "month_gross_margin_pct": gross_margin_pct,
        "month_payment_breakdown": payment_breakdown,
        "month_top_customers": top_customers,
        "month_top_products": top_products,
        "month_store_breakdown": store_breakdown,
        "month_walk_in_summary": walk_in_summary,
        "month_purchase_amount": month_purchase_amount,
        "month_purchase_qty": month_purchase_qty,
        "month_purchase_group_count": len(purchase_group_keys),
        "month_purchase_to_sales_pct": purchase_to_sales_pct,
        "month_top_suppliers": top_suppliers,
        "month_receivables_added": month_receivables_added,
        "month_receipts_collected": month_receipts_collected,
        "open_receivable_balance": open_receivable_balance,
        "open_invoice_count": open_invoice_count,
        "overdue_invoice_count": overdue_invoice_count,
        "receivable_collection_ratio_pct": receivable_collection_ratio_pct,
        "current_stock_units": current_stock_units,
        "current_stock_cost": current_stock_cost,
        "current_stock_sku_count": current_stock_sku_count,
        "perfume_stock_units": perfume_stock_units,
        "perfume_stock_cost": perfume_stock_cost,
        "perfume_stock_sku_count": perfume_stock_sku_count,
        "capital_locked_products": capital_locked_products,
        "slow_movers": slow_movers,
        "month_fast_movers": fast_movers,
        "best_sales_day": best_sales_day,
        "chart_data": chart_data,
    }
