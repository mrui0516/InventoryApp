"""Customer accounts on the wholesale catalogue: invite, activate, log in, stay inside."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from stock.models import AccountLink, CartLine, Customer, CustomerAccount, OrderRequest, Sale, SaleOrder
from stock.services import portal
from stock.test_wholesale_orders import PagesMixin

PASSWORD = "Loja-Segura-2026"


class PortalMixin(PagesMixin):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.shop = Customer.objects.create(name="Loja Ana", kind="revenda", phone="912 345 678",
                                            address="Rua 2", wholesale_discount=D("4"),
                                            wholesale_discount_set_at=timezone.now())

    def invite(self, customer=None):
        self.client.force_login(self.boss)
        self.client.post(reverse("customer_portal", args=[(customer or self.shop).pk]), {"action": "invite"})
        return self.client.session.get("portal_link")

    def token_of(self, link):
        return link.rstrip("/").split("/")[-1]

    def activate(self, link=None, phone="912345678", password=PASSWORD, again=None):
        link = link or self.invite()
        self.client.logout()
        return self.client.post(reverse("portal_invite", args=[self.token_of(link)]),
                                {"phone": phone, "password1": password, "password2": again or password})

    def customer_user(self):
        return CustomerAccount.objects.get(customer=self.shop).user


class InviteTests(PortalMixin):
    def test_a_manager_makes_a_link(self):
        link = self.invite()
        account = CustomerAccount.objects.get(customer=self.shop)
        self.assertEqual((account.status, account.phone), ("invited", "351912345678"))
        self.assertIn("/b2b/invite/", link)
        self.assertContains(self.client.get(reverse("customer_detail", args=[self.shop.pk])), link)

    def test_the_link_is_stored_as_a_digest(self):
        token = self.token_of(self.invite())
        self.assertFalse(AccountLink.objects.filter(token_hash=token).exists())
        self.assertTrue(AccountLink.objects.filter(token_hash=portal.digest(token)).exists())

    def test_a_new_link_ends_the_old_one(self):
        first = self.invite()
        self.invite()
        response = self.activate(first)
        self.assertContains(response, "não é válido")
        self.assertEqual(CustomerAccount.objects.get().status, "invited")

    def test_no_phone_no_invite(self):
        Customer.objects.filter(pk=self.shop.pk).update(phone="")
        self.invite()
        self.assertFalse(CustomerAccount.objects.exists())

    def test_only_a_revenda(self):
        retail = Customer.objects.create(name="Retail", kind="retail", phone="913000000")
        self.invite(retail)
        self.assertFalse(CustomerAccount.objects.exists())

    def test_one_phone_one_account(self):
        self.invite()
        twin = Customer.objects.create(name="Twin", kind="revenda", phone="+351 912 345 678")
        self.invite(twin)
        self.assertEqual(CustomerAccount.objects.count(), 1)

    def test_an_employee_cannot(self):
        self.client.force_login(get_user_model().objects.create_user("emp", password="pw"))
        self.client.post(reverse("customer_portal", args=[self.shop.pk]), {"action": "invite"})
        self.assertFalse(CustomerAccount.objects.exists())


class ActivateTests(PortalMixin):
    def test_the_link_page_names_the_shop_and_customer(self):
        link = self.invite()
        self.client.logout()
        self.assertContains(self.client.get(reverse("portal_invite", args=[self.token_of(link)])), "Loja Ana")

    def test_activating_logs_in_for_14_days(self):
        response = self.activate()
        self.assertRedirects(response, reverse("catalog"), fetch_redirect_response=False)
        account = CustomerAccount.objects.get()
        self.assertEqual(account.status, "active")
        self.assertTrue(account.user.check_password(PASSWORD))
        self.assertEqual(self.client.session.get_expiry_age(), 14 * 24 * 3600)

    def test_the_phone_must_be_the_customers(self):
        self.assertContains(self.activate(phone="919999999"), "telemóvel")
        self.assertEqual(CustomerAccount.objects.get().status, "invited")

    def test_the_passwords_must_match_and_be_strong(self):
        self.activate(again="Outra-Coisa-2026")
        self.activate(password="123")
        self.assertEqual(CustomerAccount.objects.get().status, "invited")

    def test_a_link_works_once(self):
        link = self.invite()
        self.activate(link)
        self.assertContains(self.activate(link), "não é válido")

    def test_a_link_lasts_72_hours(self):
        link = self.invite()
        AccountLink.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertContains(self.activate(link), "não é válido")


class LoginTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.activate()
        self.client.logout()

    def login(self, phone="912345678", password=PASSWORD):
        return self.client.post(reverse("portal_login"), {"phone": phone, "password": password})

    def test_phone_and_password(self):
        self.assertRedirects(self.login(phone="+351 912 345 678"), reverse("catalog"), fetch_redirect_response=False)

    def test_a_wrong_password_says_nothing_more(self):
        self.assertContains(self.login(password="nope-nope-nope"), "Telemóvel ou palavra-passe incorretos")
        self.assertContains(self.login(phone="910000000"), "Telemóvel ou palavra-passe incorretos")

    def test_five_misses_lock_it_for_a_while(self):
        for _ in range(5):
            self.login(password="nope-nope-nope")
        self.assertContains(self.login(), "Demasiadas tentativas")

    def test_a_suspended_account_cannot(self):
        CustomerAccount.objects.update(status="suspended")
        self.assertContains(self.login(), "Telemóvel ou palavra-passe incorretos")

    def test_a_staff_login_is_not_a_customer_login(self):
        response = self.client.post(reverse("portal_login"), {"phone": "boss", "password": "pw"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 200)


class StaysInsideTests(PortalMixin):
    def setUp(self):
        super().setUp()
        self.activate()

    def test_internal_pages_send_back_to_the_catalogue(self):
        for name in ("home", "product_list", "sales_records", "customer_search", "order_requests", "finance",
                     "inbound", "pedido", "business_rules", "catalog_types", "restock_requests"):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse("catalog"), fetch_redirect_response=False, msg_prefix=name)

    def test_internal_posts_are_refused(self):
        self.assertEqual(self.client.post(reverse("stock_transfers"), {"action": "settle"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("wholesale_customer"), {"customer": self.shop.pk}).status_code, 403)

    def test_the_catalogue_works_and_prices_are_theirs(self):
        page = self.client.get(reverse("catalog"))
        self.assertEqual(page.status_code, 200)
        row = next(r for r in page.context["page_obj"] if r["pk"] == self.item.pk)
        self.assertEqual(row["price"], D("96"))
        self.assertContains(page, "Loja Ana")
        self.assertNotContains(page, "erp-nav")
        self.assertNotContains(page, 'id="wc-as"')

    def test_ordering_is_for_their_shop(self):
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        order = OrderRequest.objects.get()
        self.assertEqual((order.customer, order.submitted_by), (self.shop, self.customer_user()))

    def test_another_customers_things_stay_closed(self):
        other = Customer.objects.create(name="Outra", kind="revenda")
        sale = SaleOrder.objects.create(customer=other)
        Sale.objects.create(order=sale, product=self.item, quantity=1, unit_price=D("90"), payment_method="cash")
        theirs = OrderRequest.objects.create(customer=other, contact_name="X", phone="1", subtotal=0, tier_discount=0,
                                             tier_total=0, goods_total=0, shipping_state="pickup")
        self.assertEqual(self.client.get(reverse("wholesale_sale", args=[sale.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("wholesale_order", args=[theirs.public_id])).status_code, 404)
        self.assertEqual(list(self.client.get(reverse("wholesale_orders")).context["sales"]), [])

    def test_a_restock_request_is_theirs(self):
        gone = self._product(name="Gone", units=0)
        self.client.post(reverse("restock_request", args=[gone.pk]))
        self.assertEqual(gone.restock_requests.get().requested_by, self.customer_user())

    def test_suspending_ends_the_session(self):
        CustomerAccount.objects.update(status="suspended")
        response = self.client.get(reverse("catalog"))
        self.assertRedirects(response, reverse("portal_login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout(self):
        self.client.post(reverse("portal_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class EveryoneElseTests(PortalMixin):
    def test_a_visitor_meets_the_customer_login(self):
        self.client.logout()
        response = self.client.get(reverse("catalog"))
        self.assertTrue(response["Location"].startswith(reverse("portal_login")))

    def test_staff_keep_the_preview(self):
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(reverse("catalog")), 'id="wc-as"')

    def test_b2b_opens_the_catalogue(self):
        self.client.force_login(self.boss)
        self.assertRedirects(self.client.get("/b2b/"), reverse("catalog"), fetch_redirect_response=False)


class ManageTests(PortalMixin):
    def test_suspend_and_resume(self):
        self.activate()
        self.client.force_login(self.boss)
        url = reverse("customer_portal", args=[self.shop.pk])
        self.client.post(url, {"action": "suspend"})
        self.assertEqual(CustomerAccount.objects.get().status, "suspended")
        self.client.post(url, {"action": "resume"})
        self.assertEqual(CustomerAccount.objects.get().status, "active")

    def test_a_reset_link_for_an_active_account(self):
        self.activate()
        link = self.invite()
        response = self.activate(link, password="Nova-Senha-2026")
        self.assertRedirects(response, reverse("catalog"), fetch_redirect_response=False)
        self.assertTrue(CustomerAccount.objects.get().user.check_password("Nova-Senha-2026"))

    def test_the_page_says_where_it_stands(self):
        self.activate()
        self.client.force_login(self.boss)
        page = self.client.get(reverse("customer_detail", args=[self.shop.pk]))
        self.assertContains(page, "Catalogue access")
        self.assertContains(page, "Active")
