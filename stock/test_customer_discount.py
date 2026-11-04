"""2026-11-04: what a customer last paid below wholesale carries to the next
price list and Pedido. Read from the sales: nothing is stored."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Category, Customer, Product, Sale, SaleOrder
from .services.wholesale_discount import last_discount


class LastDiscountTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Perfumes", form_kind="perfume")
        self.customer = Customer.objects.create(name="Loja", kind="revenda")
        self.a = self.bottle("A", "9950000000001", 100, "20.00")
        self.b = self.bottle("B", "9950000000002", 100, "30.00")
        self.small = self.bottle("S", "9950000000003", 30, "8.00")
        self.admin = get_user_model().objects.create_superuser("cd_admin", password="pw123456")
        self.client.force_login(self.admin)

    def bottle(self, name, barcode, volume, wholesale):
        return Product.objects.create(name=name, brand="B", barcode=barcode, category=self.cat, volume_ml=volume,
                                      default_price=Decimal("50"), wholesale_price=Decimal(wholesale))

    def sold(self, *lines, customer=None):
        order = SaleOrder.objects.create(customer=customer or self.customer)
        for product, qty, price, *rest in lines:
            Sale.objects.create(order=order, product=product, quantity=qty, unit_price=Decimal(price),
                                customer=order.customer, is_sample=bool(rest))
        return order

    def test_the_gap_of_the_last_sale(self):
        self.sold((self.a, 1, "19.00"))
        order = self.sold((self.a, 2, "17.00"))
        found = last_discount(self.customer.id)
        self.assertEqual(found["discount"], Decimal("3.00"))
        self.assertEqual(found["order_id"], order.id)

    def test_lines_that_differ_take_the_commonest_by_units_then_the_lower(self):
        self.sold((self.a, 5, "17.00"), (self.b, 1, "25.00"))
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("3.00"))
        self.sold((self.a, 1, "17.00"), (self.b, 1, "25.00"))
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("3.00"))

    def test_paying_wholesale_or_more_is_no_discount(self):
        self.sold((self.a, 1, "45.00"))
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("0.00"))

    def test_small_bottles_and_samples_do_not_count(self):
        self.sold((self.a, 1, "18.00"))
        self.sold((self.small, 3, "5.00"))
        self.sold((self.a, 1, "10.00", "sample"))
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("2.00"))

    def test_a_deleted_order_changes_the_answer(self):
        self.sold((self.a, 1, "18.00"))
        self.sold((self.a, 1, "15.00")).delete()
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("2.00"))

    def test_another_customers_sales_and_no_sales_at_all(self):
        other = Customer.objects.create(name="Outro", kind="revenda")
        self.sold((self.a, 1, "10.00"), customer=other)
        self.assertIsNone(last_discount(self.customer.id))
        self.assertIsNone(last_discount(None))

    def test_the_api(self):
        self.sold((self.a, 1, "17.50"))
        url = reverse("customer_wholesale_discount")
        self.assertEqual(self.client.get(url, {"customer": self.customer.id}).json()["discount"], "2.50")
        self.assertIsNone(self.client.get(url, {"customer": "x"}).json()["discount"])
        employee = get_user_model().objects.create_user("cd_emp", password="pw123456")
        self.client.force_login(employee)
        self.assertNotEqual(self.client.get(url, {"customer": self.customer.id}).status_code, 200)

    def test_the_search_says_which_products_take_it(self):
        found = self.client.get(reverse("products_autocomplete"), {"scope": "stock", "q": "995000000"}).json()
        flags = {r["barcode"]: r["discount_ok"] for r in found["results"]}
        self.assertEqual(flags, {"9950000000001": True, "9950000000002": True, "9950000000003": False})

    def test_the_export_and_pedido_pages_offer_it(self):
        html = self.client.get(reverse("product_list")).content.decode()
        self.assertIn('id="exportCust"', html)
        self.assertIn(reverse("customer_wholesale_discount"), html)
        from .models import Store
        session = self.client.session
        session["active_store_id"] = Store.get_default().id
        session.save()
        self.assertContains(self.client.get(reverse("pedido")), 'id="pd-disc-val"')

    # -- set by hand on the customer's page (2026-11-04) -----------------------
    def set_by_hand(self, **data):
        return self.client.post(reverse("customer_discount_set", args=[self.customer.id]), data)

    def test_a_figure_set_by_hand_counts_until_they_buy_again(self):
        self.sold((self.a, 1, "17.00"))
        self.set_by_hand(amount="5,50")
        found = last_discount(self.customer.id)
        self.assertEqual((found["discount"], found["source"]), (Decimal("5.50"), "hand"))
        self.sold((self.a, 1, "18.00"))
        found = last_discount(self.customer.id)
        self.assertEqual((found["discount"], found["source"]), (Decimal("2.00"), "sale"))

    def test_set_by_hand_with_no_sale_and_cleared_again(self):
        self.set_by_hand(amount="4")
        self.assertEqual(last_discount(self.customer.id)["discount"], Decimal("4.00"))
        self.set_by_hand(clear="1", amount="4")
        self.assertIsNone(last_discount(self.customer.id))
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.wholesale_discount)

    def test_a_bad_figure_changes_nothing(self):
        self.set_by_hand(amount="abc")
        self.set_by_hand(amount="-2")
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.wholesale_discount)

    def test_the_customer_page_shows_it_for_resellers(self):
        self.sold((self.a, 1, "17.00"))
        url = reverse("customer_detail", args=[self.customer.id])
        self.assertContains(self.client.get(url), 'value="3.00"')
        self.assertContains(self.client.get(url), "From last sale")
        self.set_by_hand(amount="2")
        page = self.client.get(url)
        self.assertContains(page, "Set by hand")
        self.assertContains(page, "Use last sale")
        retail = Customer.objects.create(name="Walk", kind="retail")
        self.assertNotContains(self.client.get(reverse("customer_detail", args=[retail.id])), "cdDisc")

    def test_only_managers_set_it(self):
        employee = get_user_model().objects.create_user("cd_emp2", password="pw123456")
        self.client.force_login(employee)
        self.set_by_hand(amount="9")
        self.customer.refresh_from_db()
        self.assertIsNone(self.customer.wholesale_discount)
