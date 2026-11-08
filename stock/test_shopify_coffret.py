from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from stock.services.shopify_catalog_client import CatalogClient
from stock.services.shopify_sync import INV_UPDATED, sync_product_price_inventory


class NonDecantTypeInventoryTests(SimpleTestCase):
    def product(self, quantity, product_type):
        return SimpleNamespace(barcode='COF', default_price=Decimal('30'),
                               product_type_id=product_type, total_stock=lambda: quantity)

    def variants(self):
        return {sku: {'product_id': 'coffret', 'variant_id': variant, 'inventory_item_id': item,
                      'price': price, 'available': quantity, 'tracked': tracked, 'policy': policy}
                for sku, variant, item, price, quantity, tracked, policy in [
                    ('COF', 'full', 'full-item', '25', -1, True, 'DENY'),
                    ('COF-10ML', '10ml', '10ml-item', '99', 10, False, 'CONTINUE'),
                    ('COF-5ML', '5ml', '5ml-item', '88', 10, False, 'CONTINUE')]}

    def test_full_product_uses_actual_stock_and_legacy_decants_are_zero_tracked_deny(self):
        for product_type, quantity in [(kind, count) for kind in ('coffret', 'oil_perfume', 'mini')
                                       for count in (0, 1, 5)]:
            with self.subTest(product_type=product_type, quantity=quantity):
                api = mock.Mock()
                result, _ = sync_product_price_inventory(
                    self.product(quantity, product_type), api, do_price=False,
                    shop_variants=self.variants(), location_id='location')
                self.assertEqual(result, INV_UPDATED)
                self.assertEqual({call.args[0]: call.args[2]
                                  for call in api.set_inventory_available.call_args_list},
                                 {'full-item': quantity, '10ml-item': 0, '5ml-item': 0})
                self.assertEqual(api.set_variant_stocked.call_args_list, [
                    mock.call('coffret', '10ml'), mock.call('coffret', '5ml')])

    def test_price_sync_updates_main_and_skips_decant_price_formulas(self):
        for product_type in ('coffret', 'oil_perfume', 'mini'):
            with self.subTest(product_type=product_type):
                api = mock.Mock()
                with mock.patch('stock.services.shopify_sync.decant_price', side_effect=AssertionError(
                        'This product type must never use decant prices')):
                    result, _ = sync_product_price_inventory(
                        self.product(5, product_type), api, do_inventory=False, shop_variants=self.variants())
                self.assertEqual(result, INV_UPDATED)
                api.update_variant_price.assert_called_once_with('coffret', 'full', '30.00')
                api.set_inventory_available.assert_not_called()


class NonDecantTypeAvailabilityTests(SimpleTestCase):
    def test_legacy_buyable_decants_never_make_non_decant_types_available(self):
        for product_type, full_quantity, expected in [
                (kind, quantity, tier) for kind in ('Coffret', 'COFFRETS', 'Óleo de perfume',
                                                   'oil_perfume', 'Mini', 'Mini perfumes')
                for quantity, tier in ((0, 2), (3, 0))]:
            with self.subTest(product_type=product_type, full_quantity=full_quantity):
                variants = [{'id': 'full', 'sku': 'COF', 'inventoryQuantity': full_quantity,
                             'inventoryPolicy': 'DENY', 'inventoryItem': {'id': 'full-item', 'tracked': True}},
                            {'id': 'decant', 'sku': 'COF-5ML', 'inventoryQuantity': 10,
                             'inventoryPolicy': 'CONTINUE', 'inventoryItem': {'id': 'decant-item', 'tracked': False}}]
                response = {'products': {'nodes': [{'id': 'coffret', 'status': 'ACTIVE',
                    'productType': product_type, 'variants': {'nodes': variants,
                        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}],
                    'pageInfo': {'hasNextPage': False, 'endCursor': None}}}
                api = CatalogClient()
                with mock.patch.object(api, 'graphql', return_value=response) as graphql:
                    records = api.all_variants_by_sku()
                self.assertEqual(api.availability_tiers['coffret'], expected)
                self.assertEqual(records['COF']['product_type'], product_type)
                self.assertIn('productType', graphql.call_args.args[0])
