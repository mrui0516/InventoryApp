# stock/models/__init__.py
"""Domain-partitioned model package for the ``stock`` app.

The models were split out of a single ``models.py`` into domain modules
(modular monolith — still one Django app, one migration history). Every
class is re-exported here so existing ``from stock.models import X`` /
``from .models import X`` imports keep working unchanged.

Domain dependency order (acyclic): core → catalog → partners → inventory
→ sales → finance → reporting → hr.
"""
from .core import BusinessSettings, PrintProfile, Store, StoreProfile
from .product_types import ProductType
from .shopify_catalog import ShopifyCatalogState, ShopifyCatalogLease
from .catalog import (Category, Brand, ProductSeries, Product, ProductContent, ProductImage, RestockRequest,
                      Concentration, FragranceFamily, Inspiration)
from .partners import Supplier, Customer
from .inventory import InboundOrder, InboundPendingItem, Purchase, StockAdjustmentLog
from .sales import (SaleOrder, Sale, SaleOrderChangeLog, SaleOrderPayment,
                    SaleOrderCharge, PedidoDraft)
from .finance import ARInvoice, ARItem, ARPayment
from .reporting import DailySalesSummary, SalesTarget
from .hr import AttendanceRecord
from .submissions import FormSubmission
from .system import ErrorLog
from .transfers import StockTransfer, StockTransferLine, StoreSettlement
from .wholesale import CartLine, OrderRequest, OrderRequestLine
from .expenses import (Expense, ExpenseCategory, ExpenseTemplate, ExpenseTemplateSkip, FinanceLog,
                       OwnerFunding, SupplierPayment)

__all__ = [
    'ShopifyCatalogState', 'ShopifyCatalogLease',
    'AccountLink', 'CustomerAccount',
    # core
    'BusinessSettings', 'PrintProfile', 'Store', 'StoreProfile',
    # wholesale
    'CartLine', 'OrderRequest', 'OrderRequestLine',
    # catalog
    'Category', 'Brand', 'ProductSeries', 'Product', 'ProductContent', 'ProductImage', 'ProductType', 'RestockRequest',
    'Concentration', 'FragranceFamily', 'Inspiration',
    # partners
    'Supplier', 'Customer',
    # inventory
    'InboundOrder', 'InboundPendingItem', 'Purchase', 'StockAdjustmentLog',
    # sales
    'SaleOrder', 'Sale', 'SaleOrderChangeLog', 'SaleOrderPayment',
    'SaleOrderCharge', 'PedidoDraft',
    # forms recorded once
    'FormSubmission', 'ErrorLog',
    # stock between the shops
    'StockTransfer', 'StockTransferLine', 'StoreSettlement',
    # finance
    'ARInvoice', 'ARItem', 'ARPayment',
    # expenses
    'Expense', 'ExpenseCategory', 'ExpenseTemplate', 'ExpenseTemplateSkip', 'FinanceLog',
    'OwnerFunding', 'SupplierPayment',
    # reporting
    'DailySalesSummary', 'SalesTarget',
    # hr
    'AttendanceRecord',
]

from .accounts import AccountLink, CustomerAccount  # noqa: E402
