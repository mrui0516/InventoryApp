from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from stock.models import Category, Product, Purchase, Store
from stock.services.store_stock import receive_batches


@override_settings(SHOPIFY_CATALOG_AUTOMATION=True, SHOPIFY_INVENTORY_SYNC=False,
                   SHOPIFY_AUTO_SYNC=False, SHOPIFY_PUSH_BACKGROUND=False)
class ArrivalQueueTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Perfumes', sync_to_shopify=True)
        self.product = Product.objects.create(name='Test', barcode='6290000001001',
                                             brand='Lattafa', category=self.category,
                                             default_price=Decimal('30'))
        self.store = Store.objects.create(name='Shop', code='shop')

    def state(self):
        from stock.models import ShopifyCatalogState
        return ShopifyCatalogState.objects.get(product=self.product)

    def receive(self, quantity=3, allocation=None):
        return receive_batches(product=self.product, quantity=quantity, cost_price=Decimal('10'),
                               allocation=allocation or {self.store.pk: quantity})

    def test_product_save_enqueues_a_durable_job(self):
        self.assertGreater(self.state().requested_revision, self.state().synced_revision)

    def test_first_receipt_is_not_a_restock(self):
        self.receive()
        self.assertIsNone(self.state().last_restocked_at)

    def test_sold_out_existing_product_gets_a_restock_timestamp(self):
        self.receive()
        Purchase.objects.filter(product=self.product).update(remaining=0)
        self.receive()
        self.assertIsNotNone(self.state().last_restocked_at)

    def test_adding_stock_while_still_available_is_not_restock(self):
        self.receive()
        self.receive()
        self.assertIsNone(self.state().last_restocked_at)

    def test_first_split_receipt_does_not_turn_second_batch_into_restock(self):
        second = Store.objects.create(name='Second', code='second')
        self.receive(quantity=4, allocation={self.store.pk: 2, second.pk: 2})
        self.assertIsNone(self.state().last_restocked_at)

    def test_receipt_and_restock_event_roll_back_together(self):
        self.receive()
        Purchase.objects.filter(product=self.product).update(remaining=0)
        before = self.state().requested_revision
        with self.assertRaises(ValueError):
            with transaction.atomic():
                self.receive()
                raise ValueError('rollback')
        self.assertIsNone(self.state().last_restocked_at)
        self.assertEqual(self.state().requested_revision, before)
        self.assertEqual(self.product.total_stock(), 0)

    def test_queue_update_rolls_back(self):
        from stock.services.shopify_catalog_queue import enqueue
        before = self.state().requested_revision
        with self.assertRaises(ValueError):
            with transaction.atomic():
                enqueue(self.product)
                raise ValueError('rollback')
        self.assertEqual(self.state().requested_revision, before)

    def test_transfer_and_refund_do_not_create_restock_events(self):
        from stock.services.store_stock import move_units
        from stock.services.stock_ops import restore_stock_fifo
        self.receive()
        second = Store.objects.create(name='Second', code='second')
        move_units(self.product, 1, self.store, second)
        self.assertIsNone(self.state().last_restocked_at)
        Purchase.objects.filter(product=self.product).update(remaining=0)
        restore_stock_fifo(self.product, 1, store=self.store)
        self.assertIsNone(self.state().last_restocked_at)

    def test_hard_delete_does_not_recreate_a_queue_row_from_image_cascade(self):
        from stock.models import ProductImage, ShopifyCatalogState
        ProductImage.objects.create(product=self.product, image='test.jpg')
        pid = self.product.pk
        Product.all_objects.filter(pk=pid).delete()
        self.assertFalse(ShopifyCatalogState.objects.filter(product_id=pid).exists())


@override_settings(SHOPIFY_CATALOG_AUTOMATION=False, SHOPIFY_INVENTORY_SYNC=False)
class StorefrontSelectionTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Perfumes', sync_to_shopify=True)

    def product(self, number, stock=3):
        p = Product.objects.create(name=f'Test {number}', barcode=f'62900000{number:05d}',
                                   brand='Lattafa', category=self.category, default_price='30')
        if stock:
            Purchase.objects.create(product=p, quantity=stock, remaining=stock, cost_price='10')
        return p

    def test_novidades_selects_newest_twenty_before_stock_ordering(self):
        from stock.services.shopify_storefront import collection_targets
        products = [self.product(n, stock=0 if n == 21 else 3) for n in range(1, 22)]
        variants = {p.barcode: {'product_id': str(p.pk), 'status': 'ACTIVE'} for p in products}
        target = collection_targets(variants)['Novidades']
        self.assertEqual(len(target), 20)
        self.assertNotIn(str(products[0].pk), target)
        self.assertIn(str(products[-1].pk), target)
        self.assertEqual(target[-1], str(products[-1].pk))

    def test_restock_expires_and_sold_out_products_leave(self):
        from stock.models import ShopifyCatalogState
        from stock.services.shopify_storefront import collection_targets
        now = timezone.now()
        good, expired, empty, new = [self.product(n, 0 if n == 3 else 3) for n in range(1, 5)]
        for p, at in [(good, now-timedelta(days=2)), (expired, now-timedelta(days=31)),
                      (empty, now-timedelta(days=1))]:
            ShopifyCatalogState.objects.create(product=p, last_restocked_at=at)
        variants = {p.barcode: {'product_id': str(p.pk), 'status': 'ACTIVE'}
                    for p in [good, expired, empty, new]}
        self.assertEqual(collection_targets(variants)['Restock'], [str(good.pk)])

    def test_drafts_and_opted_out_categories_are_not_published_by_refresh(self):
        from stock.services.shopify_storefront import collection_targets
        draft = self.product(1)
        hidden = self.product(2)
        no = Category.objects.create(name='No', sync_to_shopify=False)
        hidden.category = no
        hidden.save()
        variants = {draft.barcode: {'product_id': str(draft.pk), 'status': 'DRAFT'},
                    hidden.barcode: {'product_id': str(hidden.pk), 'status': 'ACTIVE'}}
        self.assertEqual(collection_targets(variants)['Novidades'], [])

    def test_historical_backfill_requires_a_reconciled_receipt_cycle(self):
        from stock.models import Sale, ShopifyCatalogState
        from stock.services.shopify_storefront import backfill_restocks
        p = self.product(8, stock=0)
        now = timezone.now()
        first = Purchase.objects.create(product=p, quantity=3, remaining=0, cost_price='10')
        Purchase.objects.filter(pk=first.pk).update(date=now-timedelta(days=10))
        sale = Sale.objects.create(product=p, quantity=3, unit_price='30')
        Sale.objects.filter(pk=sale.pk).update(date=now-timedelta(days=5))
        Purchase.objects.create(product=p, quantity=4, remaining=4, cost_price='10')
        self.assertEqual(backfill_restocks(), 1)
        self.assertIsNotNone(ShopifyCatalogState.objects.get(product=p).last_restocked_at)

    def test_unreconciled_journal_does_not_invent_restock_history(self):
        from stock.models import ShopifyCatalogState
        from stock.services.shopify_storefront import backfill_restocks
        p = self.product(9)
        Purchase.objects.filter(product=p).update(remaining=1)
        self.assertEqual(backfill_restocks(), 0)
        self.assertFalse(ShopifyCatalogState.objects.filter(product=p).exists())


@override_settings(SHOPIFY_CATALOG_AUTOMATION=True, SHOPIFY_INVENTORY_SYNC=False,
                   SHOPIFY_AUTO_SYNC=False, SHOPIFY_PUSH_BACKGROUND=False)
class ArrivalWorkerTests(TestCase):
    def setUp(self):
        from stock.models import ProductImage
        category = Category.objects.create(name='Perfumes', sync_to_shopify=True)
        self.product = Product.objects.create(name='Khamrah', brand='Lattafa',
                                             barcode='6290000002222', category=category,
                                             default_price=Decimal('30'), volume_ml=100)
        ProductImage.objects.create(product=self.product, image='test.jpg')
        self.api = mock.MagicMock()
        self.api.find_product_by_sku.return_value = {
            'id': 'gid://shopify/Product/1', 'status': 'ACTIVE', 'has_image': True,
            'product_type': 'Perfume', 'vendor': 'Lattafa'}
        self.api.find_product_by_handle.return_value = {
            'id': 'gid://shopify/Product/2', 'status': 'ACTIVE', 'has_image': True,
            'image_ready': True, 'image_failed': False,
            'product_type': 'Perfume', 'vendor': 'Lattafa'}
        self.api.product_set.return_value = 'gid://shopify/Product/2'
        self.api.stage_and_upload_image.return_value = 'https://cdn.example.com/image.jpg'

    def process(self):
        from stock.services.shopify_catalog_worker import process_catalog
        with mock.patch('stock.services.shopify_catalog_worker.sync_storefront', return_value={}), \
             mock.patch('stock.services.shopify_sync._local_image_path', return_value='test.jpg'), \
             mock.patch('stock.services.shopify_sync.sync_product_price_inventory', return_value=('inv_updated', 'ok')):
            return process_catalog(client=self.api)

    def state(self):
        from stock.models import ShopifyCatalogState
        return ShopifyCatalogState.objects.get(product=self.product)

    def test_existing_product_job_is_acknowledged(self):
        self.assertEqual(self.process()['failed'], 0)
        self.assertEqual(self.state().synced_revision, self.state().requested_revision)
        self.api.product_set.assert_not_called()

    @override_settings(SHOPIFY_CATALOG_AUTOMATION=False)
    def test_disabled_automation_never_processes_pending_products(self):
        self.process()
        self.api.find_product_by_sku.assert_not_called()
        self.assertLess(self.state().synced_revision, self.state().requested_revision)

    def test_new_listing_waits_for_a_ready_image(self):
        self.api.find_product_by_sku.return_value = None
        self.api.find_product_by_handle.return_value.update(
            status='DRAFT', image_ready=False, image_failed=False)
        self.assertEqual(self.process()['failed'], 1)
        self.api.set_product_status.assert_not_called()
        self.api.publish_online.assert_not_called()
        self.assertLess(self.state().synced_revision, self.state().requested_revision)

    def test_new_listing_has_a_persisted_handle_and_is_published(self):
        self.api.find_product_by_sku.return_value = None
        self.assertEqual(self.process()['failed'], 0)
        payload = self.api.product_set.call_args.args[0]
        self.assertEqual(payload['handle'], self.state().creation_handle)
        self.assertTrue(payload['handle'].endswith(self.product.barcode))
        self.assertEqual(payload['status'], 'DRAFT')
        self.api.publish_online.assert_called_once_with('gid://shopify/Product/2')

    def test_failed_api_job_remains_pending_with_backoff(self):
        from stock.services.shopify_client import ShopifyError
        self.api.find_product_by_sku.side_effect = ShopifyError('temporary')
        self.assertEqual(self.process()['failed'], 1)
        state = self.state()
        self.assertLess(state.synced_revision, state.requested_revision)
        self.assertGreater(state.next_attempt_at, timezone.now())
        self.assertEqual(state.attempts, 1)

    def test_merchant_draft_is_not_published(self):
        self.api.find_product_by_sku.return_value['status'] = 'DRAFT'
        self.process()
        self.api.publish_online.assert_not_called()
        self.api.product_set.assert_not_called()

    def test_missing_photo_does_not_create_a_listing(self):
        from stock.models import ProductImage
        ProductImage.objects.filter(product=self.product).delete()
        self.api.find_product_by_sku.return_value = None
        self.assertEqual(self.process()['failed'], 1)
        self.api.product_set.assert_not_called()

    def test_newer_generation_survives_worker_acknowledgment(self):
        from stock.services.shopify_catalog_queue import enqueue
        from stock.services.shopify_catalog_worker import process_catalog
        from stock.services import shopify_sync
        with mock.patch('stock.services.shopify_catalog_worker.sync_storefront', return_value={}), \
             mock.patch.object(shopify_sync, 'sync_product_price_inventory', side_effect=lambda *a, **k: (enqueue(self.product) or ('inv_updated', 'ok'))):
            process_catalog(client=self.api)
        self.assertLess(self.state().synced_revision, self.state().requested_revision)

    def test_newer_generation_does_not_inherit_old_failure_backoff(self):
        from stock.services.shopify_catalog_queue import enqueue
        from stock.services.shopify_client import ShopifyError
        def fail(*args, **kwargs):
            enqueue(self.product)
            raise ShopifyError('old attempt failed')
        self.api.find_product_by_sku.side_effect = fail
        self.assertEqual(self.process()['failed'], 1)
        self.assertLessEqual(self.state().next_attempt_at, timezone.now())
        self.assertEqual(self.state().attempts, 0)

    def test_database_lease_excludes_another_worker(self):
        from stock.models import ShopifyCatalogLease
        ShopifyCatalogLease.objects.create(name='catalog', token='another',
                                            expires_at=timezone.now()+timedelta(minutes=5))
        self.assertTrue(self.process()['busy'])
        self.api.find_product_by_sku.assert_not_called()

    def test_resumed_creation_uses_existing_reserved_handle(self):
        from stock.models import ShopifyCatalogState
        self.api.find_product_by_sku.return_value = None
        ShopifyCatalogState.objects.filter(product=self.product).update(creation_handle='reserved-handle')
        self.process()
        self.api.find_product_by_handle.assert_called_with('reserved-handle')
        self.api.product_set.assert_not_called()

    def test_new_coffret_does_not_create_decant_variants(self):
        from stock.models import ProductType
        self.product.product_type = ProductType.objects.get(code='coffret')
        self.product.save()
        self.api.find_product_by_sku.return_value = None
        self.process()
        self.assertEqual(len(self.api.product_set.call_args.args[0]['variants']), 1)


class CatalogLookupTests(SimpleTestCase):
    def test_inventory_write_uses_the_2026_10_input_contract(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        def graphql(query, variables):
            inventory = variables['input']
            self.assertNotIn('ignoreCompareQuantity', inventory)
            self.assertEqual(inventory['quantities'][0]['changeFromQuantity'], None)
            self.assertIn('@idempotent(key: $idempotencyKey)', query)
            self.assertTrue(variables['idempotencyKey'])
            return {'inventorySetQuantities': {'userErrors': []}}
        with mock.patch.object(api, 'graphql', side_effect=graphql):
            self.assertTrue(api.set_inventory_available('item', 'location', 0))

    def test_inventory_writes_do_not_reuse_an_old_idempotency_key(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        with mock.patch.object(api, 'graphql', return_value={'inventorySetQuantities': {'userErrors': []}}) as call:
            api.set_inventory_available('item', 'location', 0)
            api.set_inventory_available('item', 'location', 5)
        first, second = [c.args[1] for c in call.call_args_list]
        self.assertNotEqual(first['idempotencyKey'], second['idempotencyKey'])

    def test_sku_search_follows_pages_and_loads_all_parent_variants(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        variant = {'id': 'v1', 'sku': 'wanted', 'price': '30.00', 'inventoryQuantity': 4,
                   'inventoryPolicy': 'DENY', 'inventoryItem': {'id': 'i1', 'tracked': True}}
        parent = {'id': 'p1', 'title': 'Test', 'status': 'DRAFT', 'productType': 'Perfume',
                  'vendor': 'Lattafa', 'featuredMedia': {'id': 'm1', 'status': 'READY', 'mediaContentType': 'IMAGE'},
                  'variants': {'nodes': [], 'pageInfo': {'hasNextPage': True, 'endCursor': 'v-next'}}}
        def graphql(query, variables=None):
            variables = variables or {}
            if 'productVariants(' in query:
                if variables.get('after'):
                    return {'productVariants': {'nodes': [{'sku': 'wanted', 'product': {'id': 'p1'}}],
                            'pageInfo': {'hasNextPage': False, 'endCursor': 'end'}}}
                return {'productVariants': {'nodes': [{'sku': 'wanted-5ML', 'product': {'id': 'p1'}}],
                        'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}}}
            if variables.get('after') == 'v-next':
                return {'product': {'variants': {'nodes': [variant], 'pageInfo': {'hasNextPage': False}}}}
            return {'product': parent}
        with mock.patch.object(api, 'graphql', side_effect=graphql):
            result = api.find_product_by_sku('wanted')
        self.assertEqual(result['status'], 'DRAFT')
        self.assertEqual(result['variants']['wanted']['inventory_item_id'], 'i1')
        self.assertTrue(result['image_ready'])
