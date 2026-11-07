"""Shopify custom types follow current catalog rows, rather than Category."""
from unittest.mock import MagicMock

from django.test import TestCase

from stock.models import Category, Product, ProductType
from stock.services import shopify_sync


class ShopifyProductTypeTests(TestCase):
    def setUp(self):
        self.sequence = 0
        self.category = Category.objects.create(name='Perfumes', sync_to_shopify=True)
        self.client_api = MagicMock()
        self.client_api.get_location_id.return_value = 'gid://shopify/Location/1'
        self.client_api.product_set.return_value = 'gid://shopify/Product/1'

    def payload(self, kind):
        self.sequence += 1
        product = Product.objects.create(
            name='Example', brand='LATTAFA', barcode=f'629000000{self.sequence:04d}',
            category=self.category, product_type=kind, volume_ml=50,
            default_price='20.00')
        code, message = shopify_sync.create_product_in_shopify(
            product, client=self.client_api)
        self.assertEqual(code, shopify_sync.CREATED, message)
        return self.client_api.product_set.call_args.args[0]

    def test_current_catalog_type_labels_are_used_for_new_listings(self):
        for code, expected in [
            ('body_spray', 'Body spray'), ('body_mist', 'Body mist'),
            ('air_freshener', 'Ambientador'), ('coffret', 'Coffret'),
            ('mini', 'Mini'), ('oil_perfume', '\u00d3leo de perfume'),
        ]:
            with self.subTest(code=code):
                self.assertEqual(self.payload(ProductType.objects.get(code=code))['productType'], expected)

    def test_shop_defined_type_uses_its_customer_label(self):
        kind = ProductType.objects.create(code='cream', name='Cream', customer_label='Creme corporal')
        self.assertEqual(self.payload(kind)['productType'], 'Creme corporal')

    def test_blank_customer_label_falls_back_to_type_name(self):
        kind = ProductType.objects.create(code='cream', name='Cream', customer_label='')
        self.assertEqual(self.payload(kind)['productType'], 'Cream')

    def test_blank_labels_fall_back_to_the_stable_type_code(self):
        kind = ProductType.objects.create(code='cream', name='', customer_label=' ')
        self.assertEqual(self.payload(kind)['productType'], 'cream')

    def test_custom_type_is_used_outside_the_perfumes_category(self):
        self.category = Category.objects.create(name='Body care', sync_to_shopify=True)
        kind = ProductType.objects.create(code='cream', name='Cream', customer_label='Creme corporal')
        self.assertEqual(self.payload(kind)['productType'], 'Creme corporal')

    def test_editing_a_type_label_is_not_hidden_by_the_type_info_cache(self):
        kind = ProductType.objects.get(code='body_mist')
        self.assertEqual(self.payload(kind)['productType'], 'Body mist')
        ProductType.for_code(kind.code)  # Prime the cache held by this worker.
        # Another worker changes the row without clearing this worker's cache.
        ProductType.objects.filter(pk=kind.pk).update(customer_label='Bruma corporal')
        self.assertEqual(self.payload(ProductType.objects.get(pk=kind.pk))['productType'], 'Bruma corporal')
