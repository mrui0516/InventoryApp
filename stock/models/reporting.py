# stock/models/reporting.py
"""Reporting domain: pre-computed daily sales summaries and sales targets."""
from decimal import Decimal

from django.db import models


class DailySalesSummary(models.Model):
    date = models.DateField(unique=True)
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_profit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_items_sold = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"Sales on {self.date}"

    class Meta:
        ordering = ['-date']


class SalesTarget(models.Model):
    """A shop's monthly revenue goal for one category, used by the dashboard
    target widget.

    One target per shop and category, applied every month. The dashboard sums
    the targets of the selected categories - of the picked shop, or of every
    shop when all are shown - and shows progress + run-rate projection.
    """
    store = models.ForeignKey('Store', on_delete=models.CASCADE, null=True, blank=True,
                              related_name='sales_targets')
    category = models.ForeignKey(
        'Category', on_delete=models.CASCADE, related_name='sales_targets',
    )
    monthly_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['store_id', 'category__name']
        constraints = [
            models.UniqueConstraint(fields=['store', 'category'], name='uniq_sales_target_store_category'),
        ]

    def __str__(self):
        shop = f"{self.store.name} " if self.store_id else ""
        return f"{shop}{self.category.name}: EUR {self.monthly_amount:.2f}/mo"
