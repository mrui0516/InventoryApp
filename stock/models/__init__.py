# stock/models/__init__.py
"""Domain-partitioned model package for the ``stock`` app.

The models were split out of a single ``models.py`` into domain modules
(modular monolith — still one Django app, one migration history). Every
class is re-exported here so existing ``from stock.models import X`` /
``from .models import X`` imports keep working unchanged.

Domain dependency order (acyclic): core → catalog → partners → inventory
→ sales → finance → reporting → hr. Devices sit beside catalog.
"""
from .core import PrintProfile, Store, StoreProfile
from .catalog import (Category, Brand, ProductSeries, Product, ProductImage,
                      Concentration, FragranceFamily, Inspiration)
from .attributes import (CategoryAttribute, AttributeOption, ProductAttributeValue,
                         attributes_for_category, variant_attributes_for_category)
from .devices import (DeviceModel, DeviceAlias, CompatibilityGroup,
                      normalise_device_text, resolve_device, search_devices,
                      products_fitting)
from .partners import Supplier, Customer
from .inventory import InboundOrder, InboundPendingItem, Purchase, StockAdjustmentLog
from .sales import SaleOrder, Sale, SaleOrderChangeLog, SaleOrderPayment
from .finance import ARInvoice, ARItem, ARPayment
from .reporting import DailySalesSummary, SalesTarget
from .hr import AttendanceRecord

__all__ = [
    # core
    'PrintProfile', 'Store', 'StoreProfile',
    # catalog
    'Category', 'Brand', 'ProductSeries', 'Product', 'ProductImage',
    'Concentration', 'FragranceFamily', 'Inspiration',
    # devices / fitment
    'DeviceModel', 'DeviceAlias', 'CompatibilityGroup',
    # shop-defined attributes
    'CategoryAttribute', 'AttributeOption', 'ProductAttributeValue',
    'attributes_for_category', 'variant_attributes_for_category',
    'normalise_device_text', 'resolve_device', 'search_devices',
    'products_fitting',
    # partners
    'Supplier', 'Customer',
    # inventory
    'InboundOrder', 'InboundPendingItem', 'Purchase', 'StockAdjustmentLog',
    # sales
    'SaleOrder', 'Sale', 'SaleOrderChangeLog', 'SaleOrderPayment',
    # finance
    'ARInvoice', 'ARItem', 'ARPayment',
    # reporting
    'DailySalesSummary', 'SalesTarget',
    # hr
    'AttendanceRecord',
]
