"""B2B: the portal's name, one menu group, and one page with every reseller's activity."""
from datetime import timedelta
from decimal import Decimal as D

from django.urls import reverse
from django.utils import timezone

from stock.models import (AccountLink, BusinessSettings, CartLine, Customer, CustomerAccount, OrderRequest,
                          PortalVisit, Sale, SaleOrder)
from stock.models.core import forget_current
from stock.services import b2b_overview
from stock.test_portal import PASSWORD, PortalMixin


class NameTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.addCleanup(forget_current)

    def test_the_portal_is_called_scentory_b2b(self):
        self.client.logout()
        self.assertContains(self.client.get(reverse("portal_login")), "Scentory B2B")

    def test_the_name_is_set_in_shop_settings(self):
        self.client.force_login(self.boss)
        self.client.post(reverse("business_settings"), {"portal_name": "Atacado X", "purchasing_store": self.scentory.id})
        self.assertEqual(BusinessSettings.get().portal_name, "Atacado X")
        self.client.logout()
        self.assertContains(self.client.get(reverse("portal_login")), "Atacado X")


class MenuTests(PortalMixin):
    def nav(self):
        html = self.client.get(reverse("product_list")).content.decode()
        start = html.index('<nav class="erp-nav">')
        return html[start:html.index("</nav>", start)]

    def test_b2b_is_one_group(self):
        self.client.post(reverse("set_active_store"), {"store": self.khan.id})
        nav = self.nav()
        group = nav[nav.index(">B2B<"):nav.index(">Products<")]
        for name in ("b2b_overview", "order_requests", "pedido", "catalog", "restock_requests"):
            self.assertIn(f'href="{reverse(name)}"', group, name)
        self.assertEqual(nav.count(f'href="{reverse("order_requests")}"'), 1)


class OverviewMixin(PortalMixin):
    def setUp(self):
        super().setUp()
        self.activate()                          # Loja Ana: active, logged in as the customer
        self.client.get(reverse("catalog"), {"q": "yara"})
        self.client.get(reverse("catalog"), {"q": "asad"})
        self._add(quantity=3)
        self.client.force_login(self.boss)
        self.waiting = Customer.objects.create(name="Loja Nova", kind="revenda", phone="913 222 333")
        self.client.post(reverse("customer_portal", args=[self.waiting.pk]), {"action": "invite"})
        self.never = Customer.objects.create(name="Sem Conta", kind="revenda", phone="914 000 000")
        sale = SaleOrder.objects.create(customer=self.shop)
        Sale.objects.create(order=sale, product=self.item, quantity=2, unit_price=D("90"), payment_method="cash")

    def rows(self):
        return {row["customer"].name: row for row in b2b_overview.overview()["rows"]}


class ActivityTests(OverviewMixin):
    def test_a_visit_is_counted_with_what_was_searched(self):
        visit = PortalVisit.objects.get()
        self.assertGreaterEqual(visit.views, 2)
        self.assertEqual(visit.searches, ["yara", "asad"])
        self.assertIsNotNone(CustomerAccount.objects.get(customer=self.shop).last_seen_at)

    def test_the_cart_summary_and_order_fetches_are_not_visits(self):
        before = PortalVisit.objects.get().views
        self.client.force_login(self.customer_user())
        self.client.get(reverse("wholesale_cart_summary"))
        self.client.get(reverse("wholesale_pick_search"), {"q": "ya"})
        self.assertEqual(PortalVisit.objects.get().views, before)

    def test_staff_are_not_counted(self):
        self.client.get(reverse("catalog"))
        self.assertEqual(PortalVisit.objects.count(), 1)


class OverviewTests(OverviewMixin):
    def test_every_reseller_has_a_row(self):
        rows = self.rows()
        self.assertEqual((rows["Loja Ana"]["state"], rows["Loja Nova"]["state"], rows["Sem Conta"]["state"]),
                         ("active", "invited", "none"))

    def test_an_expired_invite_says_so(self):
        AccountLink.objects.update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(self.rows()["Loja Nova"]["link_expired"])

    def test_activity_cart_and_sales(self):
        row = self.rows()["Loja Ana"]
        self.assertEqual((row["days_active"], row["cart_units"]), (1, 3))
        self.assertEqual(row["cart_value"], D("270"))   # 3 x 90: the last sale (EUR 10 off) is newer than the hand-set EUR 4
        self.assertEqual((row["sales_total"], row["sales_month"]), (D("180"), D("180")))
        self.assertEqual(row["searches"], ["asad", "yara"])

    def test_online_orders(self):
        self.client.force_login(self.customer_user())
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        row = self.rows()["Loja Ana"]
        self.assertEqual((row["to_confirm"], row["last_request"].pk), (1, OrderRequest.objects.get().pk))
        self.assertEqual(row["cart_units"], 0)

    def test_the_figures_at_the_top(self):
        data = b2b_overview.overview()
        self.assertEqual((data["counts"]["active"], data["counts"]["invited"], data["counts"]["none"]), (1, 1, 1))
        self.assertEqual(data["carts"], 1)

    def test_the_page(self):
        page = self.client.get(reverse("b2b_overview"))
        for text in ("Loja Ana", "Loja Nova", "Sem Conta", "yara", "Active", "Invited", "No account"):
            self.assertContains(page, text)

    def test_the_page_filters(self):
        page = self.client.get(reverse("b2b_overview"), {"state": "invited"})
        self.assertEqual([r["customer"].name for r in page.context["rows"]], ["Loja Nova"])

    def test_a_customer_cannot_open_it(self):
        self.client.force_login(self.customer_user())
        self.assertRedirects(self.client.get(reverse("b2b_overview")), reverse("catalog"), fetch_redirect_response=False)
