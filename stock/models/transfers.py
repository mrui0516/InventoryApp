# stock/models/transfers.py
"""Stock moving between the two shops, and the money they settle for it.

Every batch belongs to one store. When goods go from one shop to the other -
by hand, or because a sale needed more than its shop had - a transfer is
written: the units move to batches of the receiving store at their original
cost and date, and the receiving store owes the sending one cost plus a fixed
markup per unit. The two shops settle that balance with each other from time
to time; a settlement is a payment from one to the other.

The markup lives only here. A moved batch keeps its real cost, so the
company's profit is the same whichever shop sells the bottle.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models


class StockTransfer(models.Model):
    MANUAL = 'manual'
    SALE = 'sale'
    OPENING = 'opening'
    REASON_CHOICES = [
        (MANUAL, 'Transfer'),
        (SALE, 'Sale needed it'),
        (OPENING, 'Opening stock'),     # the first split; no money owed
    ]

    from_store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='transfers_out')
    to_store = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='transfers_in')
    reason = models.CharField(max_length=10, choices=REASON_CHOICES, default=MANUAL, db_index=True)
    sale_order = models.ForeignKey('SaleOrder', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='stock_transfers')
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='stock_transfers')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'Transfer #{self.id} {self.from_store} -> {self.to_store}'

    @property
    def amount(self):
        return sum((line.amount for line in self.lines.all()), Decimal('0.00'))


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey('Product', on_delete=models.PROTECT, related_name='transfer_lines')
    quantity = models.PositiveIntegerField()
    cost_total = models.DecimalField(max_digits=12, decimal_places=2)     # Σ batch cost of the units
    markup_per_unit = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal('0.00'))
    amount = models.DecimalField(max_digits=12, decimal_places=2)         # what the receiver owes

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.product} x {self.quantity}'


class StoreSettlement(models.Model):
    """One shop paying the other what the transfers say it owes."""
    payer = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='settlements_paid')
    payee = models.ForeignKey('Store', on_delete=models.PROTECT, related_name='settlements_received')
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    settled_on = models.DateField(db_index=True)
    note = models.CharField(max_length=200, blank=True, default='')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='store_settlements')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-settled_on', '-id']

    def __str__(self):
        return f'{self.payer} paid {self.payee} €{self.amount}'
