from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from django.db.models import F, Max, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from ..models import ARInvoice, ARPayment, Purchase, Sale
from .profit import sale_profit_map_for_sale_ids


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
        "prev_month_value": (selected - timedelta(days=1)).strftime("%Y-%m"),
        "next_month_value": _next_month(selected).strftime("%Y-%m") if not is_current_month else None,
    }


def build_monthly_dashboard_snapshot(month_start, period_end, selected_category_ids=None, show_profit=False):
    selected_category_ids = selected_category_ids or []
    payment_labels = dict(Sale.PAYMENT_METHOD_CHOICES)

    sales_qs = (
        Sale.objects
        .annotate(business_at=Coalesce("order__created_at", "date"))
        .filter(business_at__date__gte=month_start, business_at__date__lte=period_end)
        .select_related("order__customer", "customer", "product")
        .prefetch_related("product__images")
        .order_by("business_at", "id")
    )
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

    month_receivables_added = sum(
        (invoice.total_amount or Decimal("0.00"))
        for invoice in ARInvoice.objects.filter(date__gte=month_start, date__lte=period_end)
    )
    month_receipts_collected = sum(
        (payment.amount or Decimal("0.00"))
        for payment in ARPayment.objects.filter(created_at__date__gte=month_start, created_at__date__lte=period_end)
    )

    open_receivable_balance = Decimal("0.00")
    open_invoice_count = 0
    for invoice in ARInvoice.objects.only("total_amount", "amount_paid"):
        balance = (invoice.total_amount or Decimal("0.00")) - (invoice.amount_paid or Decimal("0.00"))
        if balance > 0:
            open_receivable_balance += balance
            open_invoice_count += 1

    overdue_invoice_count = ARInvoice.objects.filter(
        due_date__lt=period_end,
        total_amount__gt=F("amount_paid"),
    ).count()
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

    strategic_insights = []
    if capital_locked_products:
        top_locked = capital_locked_products[0]
        if top_locked["rolling_45d_qty"] == 0:
            strategic_insights.append({
                "tone": "bad",
                "title": "Top stock cost is not moving",
                "body": f"{top_locked['display_name']} is tying up EUR {top_locked['stock_cost']:.2f} with no sales in the last 45 days.",
            })
        else:
            strategic_insights.append({
                "tone": "warn",
                "title": "Highest capital lock",
                "body": f"{top_locked['display_name']} is your biggest stock position at EUR {top_locked['stock_cost']:.2f}.",
            })

    if total_sales_amount and month_purchase_amount > total_sales_amount:
        strategic_insights.append({
            "tone": "warn",
            "title": "Buying is outrunning selling",
            "body": f"Purchase spend is EUR {month_purchase_amount:.2f} versus sales of EUR {total_sales_amount:.2f} in this period.",
        })

    if total_sales_amount and open_receivable_balance > (total_sales_amount * Decimal("0.35")):
        strategic_insights.append({
            "tone": "bad",
            "title": "Receivables need attention",
            "body": f"Open receivables are EUR {open_receivable_balance:.2f}, which is heavy against this period's sales.",
        })

    if top_customers and top_customers[0]["share_pct"] > Decimal("30.00"):
        strategic_insights.append({
            "tone": "warn",
            "title": "Customer concentration is high",
            "body": f"{top_customers[0]['name']} represents {top_customers[0]['share_pct']:.1f}% of period sales.",
        })

    if slow_movers:
        worst_slow = slow_movers[0]
        if worst_slow["days_since_last_sale"] is not None and worst_slow["days_since_last_sale"] > 60:
            strategic_insights.append({
                "tone": "warn",
                "title": "Slow mover worth reviewing",
                "body": f"{worst_slow['display_name']} still holds EUR {worst_slow['stock_cost']:.2f} and last sold {worst_slow['days_since_last_sale']} days ago.",
            })

    strategic_insights = strategic_insights[:4]

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
        "strategic_insights": strategic_insights,
        "best_sales_day": best_sales_day,
        "chart_data": chart_data,
    }
