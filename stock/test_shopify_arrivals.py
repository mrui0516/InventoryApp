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

    def variant(self, product, available=3):
        return {'product_id': str(product.pk), 'status': 'ACTIVE',
                'available': available, 'tracked': True, 'policy': 'DENY'}

    def test_novidades_uses_app_created_at_and_fills_twenty_around_latest_empty(self):
        from stock.services.shopify_storefront import sync_storefront
        products = [self.product(n, stock=0 if n == 21 else 3) for n in range(1, 22)]
        variants = {p.barcode: self.variant(p) for p in products}
        # Creation date wins over App PK and Shopify result order.
        Product.objects.filter(pk=products[0].pk).update(created_at=timezone.now()+timedelta(days=1))
        online = [str(p.pk) for p in products]
        newest = [str(products[0].pk)] + [str(p.pk) for p in reversed(products[1:20])]
        client = mock.Mock()
        client.all_variants_by_sku.return_value = variants
        client.online_product_ids.return_value = online
        client.all_collections.return_value = [{'title': 'Novidades', 'id': 'new'}]
        sync_storefront(client)
        client.set_collection_products.assert_any_call('new', newest)

    def test_unpublished_active_app_products_do_not_displace_online_products(self):
        from stock.services.shopify_storefront import sync_storefront
        products = [self.product(n) for n in range(1, 23)]
        client = mock.Mock()
        client.all_variants_by_sku.return_value = {
            p.barcode: self.variant(p) for p in products}
        # The two newest App records are ACTIVE but absent from Online Store.
        # An Online Store product without an App match is also ineligible.
        online = [str(p.pk) for p in products[:20]] + ['gid://shopify/Product/unmatched']
        newest = [str(p.pk) for p in reversed(products[:20])]
        client.online_product_ids.return_value = online
        client.all_collections.return_value = [{'title': 'Novidades', 'id': 'new'}]
        result = sync_storefront(client)
        self.assertEqual(result['Novidades'], 20)
        client.set_collection_products.assert_any_call('new', newest)

    def test_publication_fetch_failure_preserves_all_collections(self):
        from stock.services.shopify_client import ShopifyError
        from stock.services.shopify_storefront import sync_storefront
        client = mock.Mock()
        client.all_variants_by_sku.return_value = {}
        client.online_product_ids.side_effect = ShopifyError('temporary')
        client.all_collections.return_value = [{'title': 'Novidades', 'id': 'new'}]
        with self.assertRaises(ShopifyError):
            sync_storefront(client)
        client.create_collection.assert_not_called()
        client.publish_online.assert_not_called()
        client.set_collection_products.assert_not_called()
        client.reorder_collection_products.assert_not_called()

    def test_new_app_product_evicts_oldest_and_duplicate_shopify_ids_use_one_slot(self):
        from stock.services.shopify_storefront import collection_targets
        products = [self.product(n) for n in range(1, 21)]
        variants = {p.barcode: self.variant(p) for p in products}
        online = [str(p.pk) for p in products]
        before = collection_targets(variants, online_product_ids=online)['Novidades']
        self.assertEqual(before, [str(p.pk) for p in reversed(products)])
        newest = self.product(21)
        duplicate = self.product(22)
        same_time = timezone.now()
        Product.objects.filter(pk__in=[newest.pk, duplicate.pk]).update(created_at=same_time)
        variants.update({p.barcode: dict(self.variant(p), product_id=str(newest.pk))
                         for p in [newest, duplicate]})
        online.append(str(newest.pk))
        after = collection_targets(variants, online_product_ids=online)['Novidades']
        self.assertEqual(after, [str(newest.pk)] + before[:-1])

    def test_app_creation_ties_use_descending_primary_key(self):
        from stock.services.shopify_storefront import collection_targets
        first, second = self.product(1), self.product(2)
        Product.objects.filter(pk__in=[first.pk, second.pk]).update(created_at=timezone.now())
        variants = {p.barcode: self.variant(p)
                    for p in [first, second]}
        self.assertEqual(collection_targets(variants, online_product_ids=[str(first.pk), str(second.pk)])[
            'Novidades'], [str(second.pk), str(first.pk)])

    def test_receipt_restores_recent_empty_product_and_evicts_oldest_eligible(self):
        from stock.services.shopify_storefront import collection_targets
        products = [self.product(n, stock=0 if n == 21 else 3) for n in range(1, 22)]
        # Shopify may still have a buyable decant while App stock is exhausted.
        variants = {p.barcode: self.variant(p) for p in products}
        online = [str(p.pk) for p in products]
        before = collection_targets(variants, online_product_ids=online)['Novidades']
        self.assertEqual(before, [str(p.pk) for p in reversed(products[:20])])
        Purchase.objects.create(product=products[-1], quantity=3, remaining=3, cost_price='10')
        after = collection_targets(variants, online_product_ids=online)['Novidades']
        self.assertEqual(after, [str(products[-1].pk)] + before[:-1])

    def test_novidades_requires_buyability_and_accepts_decant_only_without_stock_tier_sorting(self):
        from stock.services.shopify_storefront import collection_targets
        full, decant, unavailable = [self.product(n) for n in range(1, 4)]
        variants = {full.barcode: self.variant(full), decant.barcode: self.variant(decant, 0),
                    decant.barcode+'-5ML': self.variant(decant, 2),
                    unavailable.barcode: self.variant(unavailable, 0)}
        self.assertEqual(collection_targets(variants, online_product_ids=[str(p.pk) for p in [
            full, decant, unavailable]])['Novidades'], [str(decant.pk), str(full.pk)])

    def test_monthly_sales_rank_within_full_bottle_decant_and_sold_out_tiers(self):
        from stock.models import Sale
        from stock.services.shopify_storefront import collection_targets, TOP_TITLE
        out, decant, full_low, full_high = [self.product(n) for n in range(1, 5)]
        variants = {}
        for product, quantity, units in [(out, 0, 50), (decant, 0, 40),
                                         (full_low, 3, 1), (full_high, 3, 2)]:
            variants[product.barcode] = {'product_id': str(product.pk), 'status': 'ACTIVE',
                                        'available': quantity, 'tracked': True, 'policy': 'DENY'}
            Sale.objects.create(product=product, quantity=units, unit_price='30')
        variants[decant.barcode+'-5ML'] = {'product_id': str(decant.pk), 'status': 'ACTIVE',
                                         'available': 5, 'tracked': True, 'policy': 'DENY'}
        self.assertEqual(collection_targets(variants)[TOP_TITLE], [str(p.pk) for p in [
            full_high, full_low, decant, out]])

    def test_ordinary_collection_reorders_complete_membership_without_replacing_it(self):
        from stock.services.shopify_storefront import sync_storefront
        client = mock.Mock()
        client.all_variants_by_sku.return_value = {
            'OUT': {'product_id': 'out', 'available': 0, 'tracked': True, 'policy': 'DENY'},
            'DEC-10ML': {'product_id': 'decant', 'available': 3, 'tracked': True, 'policy': 'DENY'},
            'FULL': {'product_id': 'full', 'available': 2, 'tracked': True, 'policy': 'DENY'}}
        client.online_product_ids.return_value = {'out', 'decant', 'full'}
        client.all_collections.return_value = [{'id': 'brand', 'title': 'Lattafa',
                                                'handle': 'lattafa', 'smart': True},
                                               {'id': 'all', 'title': 'Catalog', 'handle': 'all'}]
        # This represents every page, including the full bottle on a later page.
        client.collection_product_ids.side_effect = lambda gid: (
            ['out', 'decant', 'full', 'unmatched'] if gid == 'brand' else ['out'])
        sync_storefront(client)
        client.reorder_collection_products.assert_any_call(
            'brand', ['full', 'decant', 'out', 'unmatched'], current=['out', 'decant', 'full', 'unmatched'])
        self.assertFalse(any(call.args[0] in ('brand', 'all')
                             for call in client.set_collection_products.call_args_list))
        client.set_product_status.assert_not_called()
        self.assertFalse(any(call.args[0] in ('out', 'decant', 'full')
                             for call in client.publish_online.call_args_list))

    def test_missing_all_collection_is_never_created_or_published(self):
        from stock.services.shopify_storefront import sync_storefront
        client = mock.Mock()
        client.all_variants_by_sku.return_value = {
            'OLD': {'product_id': 'unpublished', 'available': 5, 'tracked': True, 'policy': 'DENY'},
            'OUT': {'product_id': 'out', 'available': 0, 'tracked': True, 'policy': 'DENY'},
            'FULL': {'product_id': 'full', 'available': 2, 'tracked': True, 'policy': 'DENY'}}
        client.online_product_ids.return_value = {'out', 'full'}
        client.all_collections.return_value = []
        client.create_collection.side_effect = lambda title, **kwargs: kwargs.get('handle', title)
        sync_storefront(client)
        self.assertFalse(any(call.kwargs.get('handle') == 'all'
                             for call in client.create_collection.call_args_list))
        self.assertFalse(any(call.args[0] == 'all'
                             for call in client.set_collection_products.call_args_list))
        self.assertFalse(any(call.args[0] in ('all', 'unpublished')
                             for call in client.publish_online.call_args_list))

    def test_managed_all_membership_follows_online_catalog_without_touching_products(self):
        from stock.services.shopify_storefront import sync_storefront
        client = mock.Mock()
        client.all_variants_by_sku.return_value = {}
        client.online_product_ids.return_value = {'new-online'}
        client.all_collections.return_value = [{'id': 'all', 'title': 'Todos os produtos',
                                                'handle': 'all', 'managed_all': True}]
        sync_storefront(client)
        client.set_collection_products.assert_any_call('all', ['new-online'])
        client.product_set.assert_not_called()
        self.assertFalse(any(call.args[0] == 'new-online'
                             for call in client.publish_online.call_args_list))

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

    def test_old_unlisted_product_is_not_created_by_a_queued_job(self):
        from stock.models import ShopifyCatalogState
        ShopifyCatalogState.objects.filter(product=self.product).update(allow_create=False)
        self.product.save()  # Editing an old product must not grant creation permission.
        self.api.find_product_by_sku.return_value = None
        result = self.process()
        self.assertEqual(result['skipped'], 1)
        self.api.product_set.assert_not_called()
        self.api.publish_online.assert_not_called()

    def test_old_automation_draft_is_not_published_after_the_scope_change(self):
        from stock.models import ShopifyCatalogState
        ShopifyCatalogState.objects.filter(product=self.product).update(
            allow_create=False, creation_handle='old-reserved')
        self.api.find_product_by_handle.return_value['status'] = 'DRAFT'
        self.assertEqual(self.process()['skipped'], 1)
        self.api.product_set.assert_not_called()
        self.api.publish_online.assert_not_called()

    def test_old_listed_product_still_syncs_without_creation_permission(self):
        from stock.models import ShopifyCatalogState
        ShopifyCatalogState.objects.filter(product=self.product).update(allow_create=False)
        self.assertEqual(self.process()['synced'], 1)
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


@override_settings(SHOPIFY_ONLINE_STORE_PUBLICATION_ID='gid://shopify/Publication/online')
class OnlinePublicationSelectionTests(SimpleTestCase):
    def node(self, number, published=True):
        return {'id': f'gid://shopify/Product/{number}',
                'publishedOnPublication': published}

    def page(self, nodes, more=False, cursor=None):
        return {'products': {'nodes': nodes,
                             'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}

    def test_all_online_products_are_loaded_beyond_the_first_fifty(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        pages = [self.page([self.node(n) for n in range(1, 51)], more=True, cursor='next'),
                 self.page([self.node(51)])]
        with mock.patch.object(api, 'graphql', side_effect=pages) as query:
            result = api.online_product_ids()
        self.assertEqual(len(result), 51)
        self.assertIn('gid://shopify/Product/1', result)
        self.assertIn('gid://shopify/Product/51', result)
        operation, variables = query.call_args.args
        self.assertIn('first: 50', operation)
        self.assertEqual(variables['after'], 'next')
        self.assertEqual(variables['query'], 'status:active AND published_status:published')
        self.assertEqual(variables['publicationId'], 'gid://shopify/Publication/online')

    def test_other_channel_products_are_skipped_without_hiding_online_products(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        with mock.patch.object(api, 'graphql', return_value=self.page([
                self.node(99, published=False), self.node(1)])):
            self.assertEqual(api.online_product_ids(), {'gid://shopify/Product/1'})

    def test_malformed_publication_responses_raise_instead_of_returning_empty_membership(self):
        from stock.services.shopify_catalog_client import CatalogClient
        from stock.services.shopify_client import ShopifyError
        bad_pages = [{}, {'products': None}, {'products': {'nodes': []}},
                     self.page([{'id': 'gid://shopify/Product/1'}]),
                     self.page([dict(self.node(1), publishedOnPublication=None)]),
                     self.page([], more=True),
                     self.page([self.node(1), self.node(1)])]
        for response in bad_pages:
            with self.subTest(response=response):
                with mock.patch.object(CatalogClient, 'graphql', return_value=response):
                    with self.assertRaises(ShopifyError):
                        CatalogClient().online_product_ids()

    def test_later_page_failure_does_not_return_a_partial_online_catalog(self):
        from stock.services.shopify_catalog_client import CatalogClient
        from stock.services.shopify_client import ShopifyError
        with mock.patch.object(CatalogClient, 'graphql', side_effect=[
                self.page([self.node(1)], more=True, cursor='next'), ShopifyError('temporary')]):
            with self.assertRaises(ShopifyError):
                CatalogClient().online_product_ids()

    def test_valid_empty_online_catalog_is_supported(self):
        from stock.services.shopify_catalog_client import CatalogClient
        with mock.patch.object(CatalogClient, 'graphql', return_value=self.page([])):
            self.assertEqual(CatalogClient().online_product_ids(), set())


class CatalogAvailabilityTests(SimpleTestCase):
    def test_reorder_uses_actual_manual_order_after_switching_sort_mode(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        with mock.patch.object(api, 'collection_product_ids', side_effect=[['A', 'B', 'C'], ['C', 'B', 'A']]), \
             mock.patch.object(api, '_collection_update'), \
             mock.patch.object(api, 'graphql', return_value={
                 'collectionReorderProducts': {'job': None, 'userErrors': []}}) as graphql:
            api.reorder_collection_products('ordinary', ['B', 'A', 'C'])
        self.assertEqual(graphql.call_args.args[1]['moves'], [
            {'id': 'B', 'newPosition': '0'}, {'id': 'A', 'newPosition': '1'}])

    def test_variant_metadata_and_no_sku_availability_follow_all_catalog_pages(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        def variant(sku, quantity, tracked=True, policy='DENY'):
            return {'id': 'variant-'+sku, 'sku': sku, 'inventoryQuantity': quantity,
                    'inventoryPolicy': policy, 'inventoryItem': {'id': 'item-'+sku, 'tracked': tracked}}
        def page(nodes, more=False, cursor=None):
            return {'nodes': nodes, 'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}
        def product(gid, variants):
            return {'id': gid, 'status': 'ACTIVE', 'variants': page(variants)}
        first = product('decant', [variant('DEC', 0)])
        first['variants']['pageInfo'] = {'hasNextPage': True, 'endCursor': 'variants-next'}
        def graphql(query, variables):
            self.assertIn('inventoryQuantity', query)
            self.assertIn('inventoryPolicy', query)
            self.assertIn('tracked', query)
            if 'CatalogMoreVariants' in query:
                self.assertEqual(variables['after'], 'variants-next')
                return {'product': {'variants': page([variant('DEC-5ML', 3)])}}
            if variables['cursor']:
                return {'products': page([
                    product('untracked-no-sku', [variant('', 0, tracked=False)]),
                    product('continue', [variant('PREORDER', -2, policy='CONTINUE')]),
                    product('out', [variant('OUT', 0)])])}
            return {'products': page([first], more=True, cursor='products-next')}
        with mock.patch.object(api, 'graphql', side_effect=graphql):
            variants = api.all_variants_by_sku()
        self.assertEqual(variants['DEC-5ML']['available'], 3)
        self.assertEqual(variants['OUT']['tracked'], True)
        self.assertEqual(variants['PREORDER']['policy'], 'CONTINUE')
        self.assertEqual(api.availability_tiers, {
            'decant': 1, 'untracked-no-sku': 0, 'continue': 0, 'out': 2})

    def test_variant_tiers_obey_shopify_buyability_and_authoritative_decant_skus(self):
        from stock.services.shopify_catalog_client import variant_availability_tier
        cases = [('FULL', 2, True, 'DENY', 0), ('FULL', 0, True, 'DENY', 2),
                 ('FULL', 0, False, 'DENY', 0), ('FULL', -2, True, 'CONTINUE', 0),
                 ('PERFUME-5ML', 1, True, 'DENY', 1), ('PERFUME-10ML', 0, True, 'DENY', 2),
                 ('PERFUME-10ML', 0, False, 'DENY', 1), ('PERFUME-5ML', 0, True, 'CONTINUE', 1),
                 ('PERFUME-50ML', 1, True, 'DENY', 0), ('', 1, True, 'DENY', 0)]
        for sku, quantity, tracked, policy, expected in cases:
            with self.subTest(sku=sku, quantity=quantity, tracked=tracked, policy=policy):
                self.assertEqual(variant_availability_tier(sku, {
                    'available': quantity, 'tracked': tracked, 'policy': policy}), expected)

    def test_collection_pagination_moves_later_page_stock_ahead_without_changing_membership(self):
        from stock.services.shopify_catalog_client import CatalogClient
        api = CatalogClient()
        current = [f'out-{n}' for n in range(50)] + ['full']
        def graphql(query, variables):
            if 'CatalogMembers' in query:
                if variables['after']:
                    return {'collection': {'products': {'nodes': [{'id': 'full'}],
                        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
                return {'collection': {'products': {'nodes': [{'id': p} for p in current[:50]],
                    'pageInfo': {'hasNextPage': True, 'endCursor': 'next'}}}}
            self.assertIn('CatalogReorder', query)
            self.assertEqual(variables['moves'], [{'id': 'full', 'newPosition': '0'}])
            return {'collectionReorderProducts': {'job': None, 'userErrors': []}}
        with mock.patch.object(api, 'graphql', side_effect=graphql), \
             mock.patch.object(api, '_collection_update') as update:
            loaded = api.collection_product_ids('ordinary')
            self.assertEqual(loaded, current)
            api.reorder_collection_products('ordinary', ['full']+current[:-1], current=loaded)
        update.assert_called_once_with({'id': 'ordinary', 'sortOrder': 'MANUAL'})


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
