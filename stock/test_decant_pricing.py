from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from stock.services.shopify_sync import decant_price, sync_product_price_inventory, INV_UPDATED, INV_UNCHANGED


class DecantPricingPolicyTests(SimpleTestCase):
    def test_new_ratios_and_fixed_fee_round_to_cent(self):
        cases = [('30.00','-10ML','3.00'),('30.00','-5ML','2.00'),
                 ('49.99','-10ML','5.00'),('49.99','-5ML','3.00'),
                 ('61.50','-5ML','3.58'),('61.50','-10ML','6.15')]
        for full, size, expected in cases:
            with self.subTest(full=full, size=size):
                self.assertEqual(decant_price(Decimal(full),size),Decimal(expected))

    def test_ongoing_sync_updates_existing_decants_when_full_price_already_matches(self):
        variants = {
            'B1': {'product_id':'P1','variant_id':'V0','price':'40.00'},
            'B1-10ML': {'product_id':'P1','variant_id':'V1','price':'7.00'},
            'B1-5ML': {'product_id':'P1','variant_id':'V2','price':'4.60'},
        }
        class MemoryClient:
            def update_variant_price(self, product_id, variant_id, price):
                for value in variants.values():
                    if value['product_id']==product_id and value['variant_id']==variant_id:
                        value['price']=price
        product=SimpleNamespace(barcode='B1',default_price=Decimal('40.00'))
        result,_=sync_product_price_inventory(product,MemoryClient(),do_inventory=False,shop_variants=variants)
        self.assertEqual(result,INV_UPDATED)
        self.assertEqual([variants[k]['price'] for k in variants],['40.00','4.00','2.50'])
        again,_=sync_product_price_inventory(product,MemoryClient(),do_inventory=False,shop_variants=variants)
        self.assertEqual(again,INV_UNCHANGED)

    def test_price_only_dry_run_never_writes_or_reads_inventory(self):
        class NoWriteClient:
            def update_variant_price(self,*args):
                raise AssertionError('dry run wrote a price')
        product=SimpleNamespace(barcode='B1',default_price=Decimal('40.00'))
        variants={'B1':{'product_id':'P1','variant_id':'V0','price':'40.00'},
                  'B1-5ML':{'product_id':'P1','variant_id':'V1','price':'4.60'}}
        result,detail=sync_product_price_inventory(product,NoWriteClient(),do_inventory=False,dry_run=True,shop_variants=variants)
        self.assertIn('2.50',detail)
        self.assertEqual(variants['B1-5ML']['price'],'4.60')
