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
    """Per-category monthly revenue goal, used by the dashboard target widget.

    One target per category, applied every month. The dashboard sums the
    targets of the currently-selected categories (or all defined targets when
    no category filter is active) and shows progress + run-rate projection.
    """
    category = models.OneToOneField(
        'Category', on_delete=models.CASCADE, related_name='sales_target',
    )
    monthly_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category__name']

    def __str__(self):
        return f"{self.category.name}: EUR {self.monthly_amount:.2f}/mo"
