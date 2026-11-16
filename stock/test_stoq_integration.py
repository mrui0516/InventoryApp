import json
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from stock.services.shopify_client import ShopifyClient, ShopifyError
from stock.services import shopify_sync


class StoqStockCompatibilityTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch('stock.services.shopify_preorders.incoming_capacity', return_value=5)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sync(self, *, member=True, sku='B1', tracked=True, policy='CONTINUE', committed=0):
        p = SimpleNamespace(barcode='B1', default_price=Decimal('30'), product_type_id='perfume', total_stock=lambda: 0)
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {'gid://shopify/ProductVariant/1'} if member else set()
        client.committed_quantity.return_value = 0
        client.preorder_inventory_snapshot.return_value = {'available': 0, 'committed': committed}
        variants = {sku: {'product_id': 'P', 'variant_id': 'gid://shopify/ProductVariant/1',
                          'inventory_item_id': 'I', 'available': 0, 'tracked': tracked, 'policy': policy}}
        result = shopify_sync.sync_product_price_inventory(p, client, do_price=False,
                    shop_variants=variants, location_id='L')
        return client, result

    def test_active_stoq_whole_bottle_keeps_continue_selling(self):
        client, result = self.sync()
        self.assertEqual(result[0], shopify_sync.INV_UNCHANGED)
        client.set_variant_stocked.assert_not_called()
        client.set_inventory_available.assert_not_called()

    def test_non_preorder_and_decants_keep_deny_policy(self):
        for kwargs in [{'member': False}, {'sku': 'B1-5ML'}, {'tracked': False}]:
            client, result = self.sync(**kwargs)
            self.assertEqual(result[0], shopify_sync.INV_UPDATED)
            client.set_variant_stocked.assert_called_once()

    def test_exhausted_offer_deny_policy_is_never_reopened(self):
        client, result = self.sync(policy='DENY')
        self.assertEqual(result[0], shopify_sync.INV_UNCHANGED)
        client.set_variant_stocked.assert_not_called()

    def test_real_quantities_still_sync_for_preorder_whole_bottles(self):
        p = SimpleNamespace(barcode='B1', default_price=Decimal('30'), product_type_id='perfume', total_stock=lambda: 0)
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {'gid://shopify/ProductVariant/1'}
        client.committed_quantity.return_value = 0
        client.preorder_inventory_snapshot.return_value = {'available': 2, 'committed': 0}
        variants = {'B1': {'product_id': 'P', 'variant_id': 'gid://shopify/ProductVariant/1',
                           'inventory_item_id': 'I', 'available': 2, 'tracked': True, 'policy': 'CONTINUE'}}
        result = shopify_sync.sync_product_price_inventory(p, client, do_price=False,
                    shop_variants=variants, location_id='L')
        self.assertEqual(result[0], shopify_sync.INV_UPDATED)
        client.set_variant_stocked.assert_not_called()
        client.set_inventory_available.assert_called_once_with('I', 'L', 0, expected_available=2)

    def test_non_incoming_stoq_offer_cannot_keep_continue_selling(self):
        with mock.patch('stock.services.shopify_preorders.incoming_capacity', return_value=0):
            client, result = self.sync()
        self.assertEqual(result[0], shopify_sync.INV_UPDATED)
        client.set_variant_stocked.assert_called_once()

    def test_preorder_stops_when_all_incoming_capacity_is_committed(self):
        with mock.patch('stock.services.shopify_preorders.incoming_capacity', return_value=5):
            client, result = self.sync(committed=5)
        self.assertEqual(result[0], shopify_sync.INV_UPDATED)
        client.set_variant_stocked.assert_called_once()

    def test_committed_preorders_are_not_reset_to_zero_or_resold_on_arrival(self):
        p = SimpleNamespace(barcode='B1', default_price=Decimal('30'), product_type_id='perfume', total_stock=lambda: 5)
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {'gid://shopify/ProductVariant/1'}
        client.committed_quantity.return_value = 3
        client.preorder_inventory_snapshot.return_value = {'available': -3, 'committed': 3}
        variants = {'B1': {'product_id': 'P', 'variant_id': 'gid://shopify/ProductVariant/1',
                           'inventory_item_id': 'I', 'available': -3, 'tracked': True, 'policy': 'CONTINUE'}}
        shopify_sync.sync_product_price_inventory(p, client, do_price=False, shop_variants=variants, location_id='L')
        client.set_inventory_available.assert_called_once_with('I', 'L', 2, expected_available=-3)
        client.set_variant_stocked.assert_not_called()


class StoqOfferReadTests(SimpleTestCase):
    def test_inventory_snapshot_and_compare_and_swap_preserve_concurrent_orders(self):
        from stock.services.shopify_catalog_client import CatalogClient
        for cls, field in [(ShopifyClient, 'compareQuantity'), (CatalogClient, 'changeFromQuantity')]:
            api = cls()
            snapshot = {'inventoryItem': {'inventoryLevel': {'quantities': [
                {'name': 'available', 'quantity': -3}, {'name': 'committed', 'quantity': 3}]}}}
            with mock.patch.object(api, 'graphql', return_value=snapshot):
                self.assertEqual(api.preorder_inventory_snapshot('I', 'L'), {'available': -3, 'committed': 3})
            with mock.patch.object(api, 'graphql', return_value={'inventorySetQuantities': {'userErrors': []}}) as call:
                api.set_inventory_available('I', 'L', 2, expected_available=-3)
                self.assertEqual(call.call_args.args[1]['input']['quantities'][0][field], -3)
            with mock.patch.object(api, 'graphql', return_value={'inventorySetQuantities': {
                    'userErrors': [{'message': 'Quantity changed since the read'}]}}):
                with self.assertRaises(ShopifyError):
                    api.set_inventory_available('I', 'L', 2, expected_available=-3)

    def test_partial_payment_offer_is_ineligible_but_retained_for_existing_commitments(self):
        api = ShopifyClient()
        offers = [{'enabled': True, 'payment_type': 'partial', 'variant_ids': ['123']}]
        with mock.patch.object(api, 'graphql', return_value={'shop': {'offers': {'value': json.dumps(offers)}}}):
            self.assertEqual(api.active_preorder_variant_ids(), set())
            self.assertEqual(api.preorder_variant_ids(), {'gid://shopify/ProductVariant/123'})

    def test_offer_cache_expires_for_disabled_or_exhausted_campaigns(self):
        api = ShopifyClient()
        clock = [0]
        response = {'shop': {'offers': {'value': json.dumps([{'enabled': True, 'payment_type': 'full', 'variant_ids': ['123']}])}}}
        with mock.patch.object(api, 'graphql', return_value=response) as call, \
                mock.patch('stock.services.shopify_client.time.monotonic', side_effect=lambda: clock[0]):
            self.assertTrue(api.active_preorder_variant_ids())
            clock[0] = 20
            self.assertTrue(api.active_preorder_variant_ids())
            call.return_value = {'shop': {'offers': {'value': '[]'}}}
            clock[0] = 31
            self.assertEqual(api.active_preorder_variant_ids(), set())
        self.assertEqual(call.call_count, 2)

    def test_committed_inventory_reads_zero_and_rejects_missing_or_invalid_counts(self):
        for rows, expected in [([{'name': 'committed', 'quantity': 0}], 0),
                               ([{'name': 'committed', 'quantity': 3}], 3),
                               ([{'name': 'committed', 'quantity': -1}], None), ([], None)]:
            api = ShopifyClient()
            response = {'inventoryItem': {'inventoryLevel': {'quantities': rows}}}
            with mock.patch.object(api, 'graphql', return_value=response):
                if expected is None:
                    with self.assertRaises(ShopifyError): api.committed_quantity('i', 'l')
                else:
                    self.assertEqual(api.committed_quantity('i', 'l'), expected)

    def test_only_enabled_valid_variant_ids_are_accepted(self):
        api = ShopifyClient()
        offers = [{'enabled': True, 'payment_type': 'full', 'variant_ids': [123, '456', 'invalid']},
                  {'enabled': False, 'variant_ids': ['789']}]
        response = {'shop': {'offers': {'value': json.dumps(offers)}}}
        with mock.patch.object(api, 'graphql', return_value=response) as call:
            self.assertEqual(api.active_preorder_variant_ids(),
                             {'gid://shopify/ProductVariant/123', 'gid://shopify/ProductVariant/456'})
            self.assertEqual(api.active_preorder_variant_ids(),
                             {'gid://shopify/ProductVariant/123', 'gid://shopify/ProductVariant/456'})
        self.assertEqual(call.call_count, 1)

    def test_missing_offer_is_empty_and_malformed_offer_aborts_safely(self):
        for value, expected in [(None, set()), ({'value': '{bad-json'}, None)]:
            api = ShopifyClient()
            with mock.patch.object(api, 'graphql', return_value={'shop': {'offers': value}}):
                if expected is None:
                    with self.assertRaises(ShopifyError): api.active_preorder_variant_ids()
                else:
                    self.assertEqual(api.active_preorder_variant_ids(), expected)


@override_settings(SHOPIFY_INCOMING_SYNC=False, SHOPIFY_CATALOG_AUTOMATION=False,
                   SHOPIFY_INVENTORY_SYNC=False)
class IncomingPreorderEligibilityTests(TestCase):
    def setUp(self):
        from stock.models import Category, InboundOrder, InboundPendingItem, Product
        self.category = Category.objects.create(name='Perfumes', sync_to_shopify=True)
        self.order = InboundOrder.objects.create(status='pending_receipt')
        self.product = Product.objects.create(barcode='NEW1', name='New perfume',
            category=self.category, default_price=Decimal('30'))
        InboundPendingItem.objects.create(inbound_order=self.order, product=self.product,
            quantity=6, cost_price=Decimal('5'))

    def test_capacity_is_only_for_new_pending_products_and_reserves_samples(self):
        from stock.services.shopify_preorders import incoming_capacity
        self.assertEqual(incoming_capacity(self.product, {'NEW1', 'NEW1-5ML'}), 4)
        self.assertEqual(incoming_capacity(self.product, {'NEW1'}), 6)
        self.order.status = 'received'; self.order.save()
        self.assertEqual(incoming_capacity(self.product, {'NEW1'}), 0)

    def test_restocked_and_previously_sold_products_are_not_preorders(self):
        from stock.models import Purchase
        from stock.services.shopify_preorders import incoming_capacity
        Purchase.objects.create(product=self.product, quantity=1, remaining=0, cost_price=Decimal('5'))
        self.assertEqual(incoming_capacity(self.product, {'NEW1'}), 0)

    def test_periodic_guard_closes_other_offers_decants_and_exhausted_capacity(self):
        from stock.services.shopify_preorders import reconcile_preorder_policies
        rows = [
            {'id': 'gid://shopify/ProductVariant/1', 'sku': 'NEW1', 'inventoryPolicy': 'CONTINUE',
             'inventoryItem': {'id': 'I1', 'tracked': True}, 'product': {'id': 'P', 'variants': {'nodes': [{'sku': 'NEW1'}, {'sku': 'NEW1-5ML'}]}}},
            {'id': 'gid://shopify/ProductVariant/2', 'sku': 'NEW1-5ML', 'inventoryPolicy': 'CONTINUE',
             'inventoryItem': {'id': 'I2', 'tracked': True}, 'product': {'id': 'P', 'variants': {'nodes': []}}},
            {'id': 'gid://shopify/ProductVariant/3', 'sku': 'OLD', 'inventoryPolicy': 'CONTINUE',
             'inventoryItem': {'id': 'I3', 'tracked': True}, 'product': {'id': 'O', 'variants': {'nodes': []}}},
        ]
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {r['id'] for r in rows}
        client.preorder_variant_ids.return_value = {r['id'] for r in rows}
        client.graphql.return_value = {'nodes': rows}
        client.committed_quantity.return_value = 3
        result = reconcile_preorder_policies(client)
        self.assertEqual(set(result['denied']), {rows[1]['id'], rows[2]['id']})
        client.set_inventory_available.assert_not_called()
        client.committed_quantity.return_value = 4
        result = reconcile_preorder_policies(client)
        self.assertEqual(set(result['denied']), {r['id'] for r in rows})
        client.set_inventory_available.assert_not_called()

    def test_receipt_closes_offer_without_waiting_for_a_stock_change(self):
        from stock.services.shopify_preorders import reconcile_preorder_policies
        self.order.status = 'received'; self.order.save()
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {'gid://shopify/ProductVariant/1'}
        client.preorder_variant_ids.return_value = {'gid://shopify/ProductVariant/1'}
        client.graphql.return_value = {'nodes': [{'id': 'gid://shopify/ProductVariant/1',
            'sku': 'NEW1', 'inventoryPolicy': 'CONTINUE', 'inventoryItem': {'id': 'I', 'tracked': True},
            'product': {'id': 'P', 'variants': {'nodes': [{'sku': 'NEW1'}]}}}]}
        self.assertEqual(reconcile_preorder_policies(client)['denied'], ['gid://shopify/ProductVariant/1'])
        client.set_inventory_available.assert_not_called()

    def test_already_disabled_and_denied_offer_is_remembered_before_any_stock_write(self):
        from stock.models import ShopifyCatalogState
        from stock.services.shopify_preorders import reconcile_preorder_policies
        variant_id = 'gid://shopify/ProductVariant/1'
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = set()
        client.preorder_variant_ids.return_value = {variant_id}
        client.graphql.return_value = {'nodes': [{'id': variant_id,
            'sku': 'NEW1', 'inventoryPolicy': 'DENY', 'inventoryItem': {'id': 'I', 'tracked': True},
            'product': {'id': 'P', 'variants': {'nodes': [{'sku': 'NEW1'}]}}}]}
        self.assertEqual(reconcile_preorder_policies(client)['denied'], [])
        self.assertEqual(ShopifyCatalogState.objects.get(product=self.product).preorder_variant_gid, variant_id)
        client.set_inventory_available.assert_not_called()

    def test_deleted_offer_is_still_closed_by_the_periodic_guard(self):
        from stock.models import ShopifyCatalogState
        from stock.services.shopify_preorders import reconcile_preorder_policies
        variant_id = 'gid://shopify/ProductVariant/1'
        ShopifyCatalogState.objects.create(product=self.product, preorder_variant_gid=variant_id)
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = set()
        client.preorder_variant_ids.return_value = set()
        client.graphql.return_value = {'nodes': [{'id': variant_id,
            'sku': 'NEW1', 'inventoryPolicy': 'CONTINUE', 'inventoryItem': {'id': 'I', 'tracked': True},
            'product': {'id': 'P', 'variants': {'nodes': [{'sku': 'NEW1'}]}}}]}
        self.assertEqual(reconcile_preorder_policies(client)['denied'], [variant_id])
        client.set_inventory_available.assert_not_called()

    def test_disabled_or_deleted_offer_still_reserves_prepaid_unfulfilled_units(self):
        variant_id = 'gid://shopify/ProductVariant/1'
        client = mock.Mock()
        client.active_preorder_variant_ids.return_value = {variant_id}
        client.preorder_variant_ids.return_value = {variant_id}
        client.preorder_inventory_snapshot.return_value = {'available': 0, 'committed': 3}
        rec = {'product_id': 'P', 'variant_id': variant_id, 'inventory_item_id': 'I',
               'available': 0, 'tracked': True, 'policy': 'CONTINUE'}
        shopify_sync.sync_product_price_inventory(self.product, client, do_price=False,
            shop_variants={'NEW1': rec}, location_id='L')
        client.active_preorder_variant_ids.return_value = set()
        client.preorder_variant_ids.return_value = set()
        client.set_inventory_available.reset_mock()
        client.preorder_inventory_snapshot.return_value = {'available': -3, 'committed': 3}
        rec.update(policy='DENY', available=-3)
        self.order.status = 'received'; self.order.save()
        with mock.patch.object(self.product, 'total_stock', return_value=5):
            shopify_sync.sync_product_price_inventory(self.product, client, do_price=False,
                shop_variants={'NEW1': rec}, location_id='L')
        client.set_inventory_available.assert_called_once_with('I', 'L', 2, expected_available=-3)
