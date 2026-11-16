import json
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from stock.models import Category, InboundOrder, InboundPendingItem, Product, Purchase, Sale, ShopifyCatalogLease


@override_settings(SHOPIFY_INCOMING_SYNC=False)
class IncomingProductsTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Perfumes', sync_to_shopify=True)
        self.order = InboundOrder.objects.create(status='pending_receipt')

    def product(self, code, **kwargs):
        p = Product.objects.create(brand='Lattafa', name=code, barcode=code,
                                   category=self.category, default_price=Decimal('20'), **kwargs)
        InboundPendingItem.objects.create(inbound_order=self.order, product=p, quantity=3, cost_price=Decimal('5'))
        return p

    def selected(self):
        from stock.services.shopify_incoming import incoming_products
        return list(incoming_products())

    def test_only_new_products_on_pending_orders_qualify(self):
        p = self.product('NEW')
        old = self.product('RESTOCK')
        Purchase.objects.create(product=old, quantity=1, remaining=0, cost_price=Decimal('5'))
        Product.objects.create(brand='Lattafa', name='Not ordered', barcode='ZERO', category=self.category)
        self.assertEqual([x.pk for x in self.selected()], [p.pk])

    def test_receipt_and_cancellation_remove_the_product(self):
        p = self.product('NEW')
        self.assertEqual(len(self.selected()), 1)
        self.order.status = 'received'; self.order.save()
        self.assertEqual(self.selected(), [])
        self.order.status = 'pending_receipt'; self.order.save()
        self.assertEqual(len(self.selected()), 1)
        InboundPendingItem.objects.filter(product=p).delete()
        self.assertEqual(self.selected(), [])

    def test_old_sales_archives_and_disabled_categories_do_not_appear(self):
        sold = self.product('SOLD')
        Sale.objects.create(product=sold, quantity=1, unit_price=Decimal('20'))
        archived = self.product('ARCHIVED')
        Product.all_objects.filter(pk=archived.pk).update(archived_at=timezone.now())
        offline = self.product('OFFLINE')
        disabled = Category.objects.create(name='Offline', sync_to_shopify=False)
        Product.objects.filter(pk=offline.pk).update(category=disabled)
        self.assertEqual(self.selected(), [])

    def test_duplicate_pending_lines_do_not_duplicate_cards(self):
        p = self.product('NEW')
        InboundPendingItem.objects.create(inbound_order=self.order, product=p, quantity=2, cost_price=Decimal('5'))
        self.assertEqual([x.pk for x in self.selected()], [p.pk])

    def test_writer_preserves_canonical_shopify_name_and_hides_unpublished_link(self):
        self.product('NEW')
        client = mock.Mock()
        client.find_product_by_sku.return_value = {'id': 'gid://shopify/Product/1'}
        client.graphql.side_effect = [
            {'shop': {'id': 'gid://shopify/Shop/1', 'incoming': {'compareDigest': 'original'}},
             'metafieldDefinitions': {'nodes': [{'id': 'd', 'type': {'name': 'json'}, 'access': {'storefront': 'PUBLIC_READ'}}]}},
            {'nodes': [{'id': 'gid://shopify/Product/1', 'title': 'Verified name 60ml', 'vendor': 'Lattafa',
                        'status': 'ACTIVE', 'onlineStoreUrl': None, 'featuredMedia': {'image': {'url': 'https://cdn.shopify.com/new.jpg', 'altText': None}}}]},
            {'metafieldsSet': {'metafields': [{'id': 'm'}], 'userErrors': []}},
        ]
        from stock.services.shopify_incoming import sync_incoming
        result = sync_incoming(client=client)
        self.assertEqual(result['products'], 1)
        written = client.graphql.call_args_list[-1].args[1]['metafields'][0]
        self.assertEqual(written['compareDigest'], 'original')
        payload = json.loads(written['value'])
        self.assertEqual(payload['products'][0]['title'], 'Verified name 60ml')
        self.assertEqual(payload['products'][0]['url'], '')
        self.assertNotIn('cost', written['value'])
        self.assertNotIn('quantity', written['value'])
        client.publish_online.assert_not_called()
        client.set_product_status.assert_not_called()

    def test_dry_run_never_calls_shopify(self):
        self.product('NEW'); client = mock.Mock()
        from stock.services.shopify_incoming import sync_incoming
        self.assertEqual(sync_incoming(client=client, dry_run=True)['products'], 1)
        self.assertEqual(client.mock_calls, [])

    def test_empty_inbound_list_clears_previous_teasers(self):
        client = mock.Mock()
        client.graphql.side_effect = [
            {'shop': {'id': 'gid://shopify/Shop/1', 'incoming': {'compareDigest': 'old'}},
             'metafieldDefinitions': {'nodes': [{'type': {'name': 'json'}, 'access': {'storefront': 'PUBLIC_READ'}}]}},
            {'metafieldsSet': {'metafields': [{'id': 'm'}], 'userErrors': []}},
        ]
        from stock.services.shopify_incoming import sync_incoming
        self.assertEqual(sync_incoming(client=client)['products'], 0)
        field = client.graphql.call_args_list[-1].args[1]['metafields'][0]
        self.assertEqual(json.loads(field['value'])['products'], [])

    def test_failed_compare_and_set_releases_lease_for_retry(self):
        client = mock.Mock()
        client.graphql.side_effect = [
            {'shop': {'id': 'gid://shopify/Shop/1', 'incoming': {'compareDigest': 'old'}},
             'metafieldDefinitions': {'nodes': [{'type': {'name': 'json'}, 'access': {'storefront': 'PUBLIC_READ'}}]}},
            {'metafieldsSet': {'metafields': [], 'userErrors': [{'message': 'Compare digest changed'}]}},
        ]
        from stock.services.shopify_client import ShopifyError
        from stock.services.shopify_incoming import sync_incoming
        with self.assertRaises(ShopifyError):
            sync_incoming(client=client)
        self.assertEqual(ShopifyCatalogLease.objects.get(name='incoming').token, '')

    @override_settings(SHOPIFY_CATALOG_AUTOMATION=True, SHOPIFY_INCOMING_SYNC=True,
                       SHOPIFY_PUSH_BACKGROUND=False)
    def test_existing_catalog_task_refreshes_incoming_without_a_second_schedule(self):
        from stock.services.shopify_catalog_worker import process_catalog
        client = mock.Mock(lease_heartbeat=None)
        with mock.patch('stock.services.shopify_catalog_worker.sync_storefront', return_value={}), \
                mock.patch('stock.services.shopify_incoming.sync_incoming', return_value={'products': 0}) as sync:
            result = process_catalog(client=client)
        sync.assert_called_once_with(client=client)
        self.assertEqual(result['incoming']['products'], 0)

    @override_settings(SHOPIFY_INCOMING_SYNC=True)
    def test_pending_changes_wake_only_after_commit(self):
        with mock.patch('stock.services.shopify_incoming.wake_incoming') as wake:
            with self.captureOnCommitCallbacks(execute=True):
                self.product('NEW')
                wake.assert_not_called()
            self.assertTrue(wake.called)
