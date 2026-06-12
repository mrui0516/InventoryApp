from decimal import Decimal

from ..models import Purchase


def build_inventory_snapshot(product=None):
    inventory_rows = list(
        Purchase.objects
        .filter(remaining__gt=0)
        .select_related("product", "product__category")
    )

    current_stock_units = sum((row.remaining or 0) for row in inventory_rows)
    current_stock_cost = sum(
        (((row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0)) for row in inventory_rows),
        Decimal("0.00"),
    )
    current_stock_sku_count = len({row.product_id for row in inventory_rows})

    perfume_rows = [
        row for row in inventory_rows
        if getattr(getattr(row.product, "category", None), "name", "").lower() == "perfumes"
    ]
    perfume_stock_units = sum((row.remaining or 0) for row in perfume_rows)
    perfume_stock_cost = sum(
        (((row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0)) for row in perfume_rows),
        Decimal("0.00"),
    )
    perfume_stock_sku_count = len({row.product_id for row in perfume_rows})

    snapshot = {
        "current_stock_units": current_stock_units,
        "current_stock_cost": str(current_stock_cost.quantize(Decimal("0.01"))),
        "current_stock_sku_count": current_stock_sku_count,
        "perfume_stock_units": perfume_stock_units,
        "perfume_stock_cost": str(perfume_stock_cost.quantize(Decimal("0.01"))),
        "perfume_stock_sku_count": perfume_stock_sku_count,
    }

    if product is not None:
        product_rows = [row for row in inventory_rows if row.product_id == product.id]
        product_total_stock = sum((row.remaining or 0) for row in product_rows)
        product_stock_cost = sum(
            (((row.cost_price or Decimal("0.00")) * Decimal(row.remaining or 0)) for row in product_rows),
            Decimal("0.00"),
        )
        snapshot.update({
            "product_id": product.id,
            "product_total_stock": product_total_stock,
            "product_stock_cost": str(product_stock_cost.quantize(Decimal("0.01"))),
            "product_batch_count": len(product_rows),
        })

    return snapshot
