"""The shop handling wholesale orders, opening an order in Pedido, and test accounts."""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.urls import reverse

from stock.models import BusinessSettings, CartLine, OrderRequest, PedidoDraft, PrintProfile, RestockRequest
from stock.test_wholesale_orders import PagesMixin


class FlowBase(PagesMixin):
    def send(self, user=None):
        if user is not None:
            self.client.force_login(user)
        self._add()
        fingerprint = self.client.get(reverse("wholesale_cart")).context["quote"]["fingerprint"]
        self.client.post(reverse("wholesale_cart_submit"), {**self.form, "fingerprint": fingerprint})
        return OrderRequest.objects.latest("id")


class WholesaleShopTests(FlowBase):
    def setUp(self):
        super().setUp()
        PrintProfile.objects.update_or_create(store=self.scentory, defaults={"phone": "(+351) 935 993 566"})

    def test_by_default_the_main_shop_takes_the_orders(self):
        self.assertEqual(self.send().pedido.store, self.khan)

    def test_the_chosen_shop_takes_them(self):
        BusinessSettings.objects.filter(pk=BusinessSettings.get().pk).update(wholesale_store=self.scentory)
        order = self.send()
        self.assertEqual(order.pedido.store, self.scentory)
        self.assertContains(self.client.get(reverse("wholesale_order", args=[order.public_id])), "935 993 566")

    def test_it_is_chosen_in_shop_settings(self):
        page = self.client.get(reverse("business_settings"))
        self.assertContains(page, 'name="wholesale_store"')
        self.client.post(reverse("business_settings"), {"wholesale_store": self.scentory.id,
                                                        "purchasing_store": self.scentory.id})
        self.assertEqual(BusinessSettings.get().wholesale_store, self.scentory)


class OpenInPedidoTests(FlowBase):
    def test_the_link_opens_the_order_whatever_shop_is_chosen(self):
        draft = self.send().pedido
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        response = self.client.get(reverse("pedido"), {"draft": draft.pk})
        self.assertRedirects(response, f"{reverse('pedido')}?draft={draft.pk}", fetch_redirect_response=False)
        self.assertEqual(self.client.session["active_store_id"], draft.store_id)
        page = self.client.get(reverse("pedido"), {"draft": draft.pk})
        self.assertEqual(page.context["draft"], draft)
        self.assertEqual(len(page.context["draft_rows"]), 1)

    def test_a_sold_draft_does_not_switch(self):
        draft = self.send().pedido
        PedidoDraft.objects.filter(pk=draft.pk).update(status="sold")
        session = self.client.session
        session["active_store_id"] = "all"
        session.save()
        self.assertEqual(self.client.get(reverse("pedido"), {"draft": draft.pk}).status_code, 200)


class RemoveOrderTests(FlowBase):
    def run_command(self, *args):
        out = StringIO()
        call_command(*args, stdout=out)
        return out.getvalue()

    def test_a_dry_run_changes_nothing(self):
        order = self.send()
        text = self.run_command("remove_order_request", str(order.pk))
        self.assertIn("Dry run", text)
        self.assertTrue(OrderRequest.objects.filter(pk=order.pk).exists())

    def test_apply_removes_the_order_and_its_pedido(self):
        order = self.send()
        self.run_command("remove_order_request", str(order.pk), "--apply")
        self.assertFalse(OrderRequest.objects.exists())
        self.assertFalse(PedidoDraft.objects.exists())

    def test_a_sold_order_is_refused(self):
        order = self.send()
        OrderRequest.objects.filter(pk=order.pk).update(status="completed")
        text = self.run_command("remove_order_request", str(order.pk), "--apply")
        self.assertIn("sold", text)
        self.assertTrue(OrderRequest.objects.exists())


class TestAccountTests(FlowBase):
    def run_command(self, *args, password="test-pass-123"):
        out = StringIO()
        with mock.patch("getpass.getpass", return_value=password):
            call_command(*args, stdout=out)
        return out.getvalue()

    def test_create_makes_a_manager(self):
        self.run_command("test_account", "create", "teste")
        user = get_user_model().objects.get(username="teste")
        self.assertTrue(user.check_password("test-pass-123"))
        self.assertTrue(user.groups.filter(name="Managers").exists())
        self.assertFalse(user.is_superuser)

    def test_remove_lists_then_deletes_everything_of_it(self):
        self.run_command("test_account", "create", "teste")
        tester = get_user_model().objects.get(username="teste")
        self.send(tester)
        self._add()
        RestockRequest.objects.create(product=self.item, requested_by=tester)
        text = self.run_command("test_account", "remove", "teste")
        self.assertIn("Dry run", text)
        self.assertIn("1 order request", text)
        self.run_command("test_account", "remove", "teste", "--apply")
        self.assertFalse(get_user_model().objects.filter(username="teste").exists())
        self.assertFalse(OrderRequest.objects.exists())
        self.assertFalse(PedidoDraft.objects.exists())
        self.assertFalse(CartLine.objects.exists())
        self.assertFalse(RestockRequest.objects.exists())

    def test_remove_leaves_other_people_alone(self):
        self.send()
        self.run_command("test_account", "create", "teste")
        self.run_command("test_account", "remove", "teste", "--apply")
        self.assertEqual(OrderRequest.objects.count(), 1)

    def test_a_real_account_is_not_removed(self):
        get_user_model().objects.create_user("ana", password="pw")
        text = self.run_command("test_account", "remove", "ana", "--apply")
        self.assertIn("not a test account", text)
        self.assertTrue(get_user_model().objects.filter(username="ana").exists())


class CustomerTestAccountTests(TestAccountTests):
    def test_it_logs_in_as_the_customer(self):
        from stock.models import Customer, CustomerAccount
        shop = Customer.objects.create(name="Loja Teste", kind="revenda", phone="913 000 111")
        self.run_command("test_account", "create", "teste-cliente", "--customer", str(shop.pk))
        account = CustomerAccount.objects.get(customer=shop)
        self.assertEqual((account.status, account.phone), ("active", "351913000111"))
        self.client.logout()
        response = self.client.post(reverse("portal_login"), {"phone": "913000111", "password": "test-pass-123"})
        self.assertRedirects(response, reverse("catalog"), fetch_redirect_response=False)
        self.run_command("test_account", "remove", "teste-cliente", "--apply")
        self.assertFalse(CustomerAccount.objects.exists())
        self.assertTrue(Customer.objects.filter(pk=shop.pk).exists())
