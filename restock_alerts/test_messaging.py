import json
from io import StringIO
from copy import deepcopy
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import RestockSubscription as Subscription, RestockEmail as Email, MessagingSlot
from . import services


VARIANT = {'id': 'gid://shopify/ProductVariant/12345', 'sku': '6290000000001',
           'title': '100ml', 'quantity': 0, 'available': False,
           'product_id': 'gid://shopify/Product/123', 'product_title': 'Test Perfume',
           'handle': 'test-perfume'}


class FakeShopify:
    """External API boundary; application state machines and database stay real."""
    def __init__(self):
        self.customer = {'id': 'gid://shopify/Customer/42', 'tags': ['VIP'],
                         'defaultEmailAddress': {'emailAddress': 'customer@example.com',
                                                 'marketingState': 'SUBSCRIBED', 'marketingUpdatedAt': None}}
        self.added_batches = []
        self.fail_add = False
        self.consent_writes = []
        self.scopes = ['read_products', 'write_customers']

    def is_configured(self):
        return True

    def graphql(self, query, variables=None):
        variables = variables or {}
        if 'query RestockMessagingScopes' in query:
            return {'currentAppInstallation': {'accessScopes': [{'handle':s} for s in self.scopes]}}
        if 'query RestockCustomers' in query:
            return {'customers': {'nodes': [deepcopy(self.customer)] if self.customer else [],
                                  'pageInfo': {'hasNextPage': False, 'endCursor': None}}}
        if 'query RestockCustomer(' in query:
            return {'customer': deepcopy(self.customer)}
        if 'mutation RestockCustomerCreate' in query:
            self.consent_writes.append(variables['input'])
            self.customer = {'id': 'gid://shopify/Customer/42', 'tags': [],
                             'defaultEmailAddress': {'emailAddress': variables['input']['email'],
                                                     'marketingState': 'SUBSCRIBED', 'marketingUpdatedAt': None}}
            return {'customerCreate': {'customer': self.customer, 'userErrors': []}}
        if 'mutation RestockConsent' in query:
            self.consent_writes.append(variables['input'])
            self.customer['defaultEmailAddress']['marketingState'] = 'SUBSCRIBED'
            return {'customerEmailMarketingConsentUpdate': {'customer': self.customer, 'userErrors': []}}
        if 'mutation RestockTagsAdd' in query:
            self.customer['tags'] = list(dict.fromkeys(self.customer['tags'] + variables['tags']))
            self.added_batches.append(list(variables['tags']))
            if self.fail_add:
                raise TimeoutError('response lost after remote acceptance')
            return {'tagsAdd': {'node': {'id': self.customer['id']}, 'userErrors': []}}
        if 'mutation RestockTagsRemove' in query:
            self.customer['tags'] = [t for t in self.customer['tags'] if t not in variables['tags']]
            return {'tagsRemove': {'node': {'id': self.customer['id']}, 'userErrors': []}}
        raise AssertionError('Unexpected external operation')

    def complete_flow(self, job_tag=None):
        job_tag = job_tag or next(t for t in self.customer['tags'] if t.startswith('scentory-restock-job|'))
        self.customer['tags'] = [t for t in self.customer['tags'] if t != job_tag]


@override_settings(TESTING=True, RESTOCK_NOTIFICATIONS_ENABLED=True, RESTOCK_PROVIDER='messaging',
                   RESTOCK_MESSAGING_FLOW_READY=True, RESTOCK_BACKGROUND=False,
                   SHOPIFY_STORE_DOMAIN='example.myshopify.com', SHOPIFY_ADMIN_TOKEN='test-only',
                   RESTOCK_ALLOWED_ORIGINS=['https://www.scentory.pt'])
class MessagingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.remote = FakeShopify()
        self.client_patch = mock.patch('stock.services.shopify_client.ShopifyClient.graphql', side_effect=self.remote.graphql)
        self.client_patch.start()
        self.addCleanup(self.client_patch.stop)
        self.stock_patch = mock.patch('restock_alerts.services.lookup_variant', return_value=dict(VARIANT))
        self.stock = self.stock_patch.start()
        self.addCleanup(self.stock_patch.stop)

    def subscribe(self, consent=True, **fields):
        payload = {'email': 'customer@example.com', 'sku': VARIANT['sku'], 'variant_id': '12345',
                   'marketing_consent': consent, 'consent_version': 'restock-marketing-v1', **fields}
        return self.client.post('/api/restock/subscribe/', json.dumps(payload),
                                content_type='application/json', HTTP_ORIGIN='https://www.scentory.pt')

    def make_available(self):
        self.stock.return_value = {**VARIANT, 'available': True, 'quantity': 2}

    def test_without_explicit_consent_no_customer_is_created_or_modified(self):
        self.remote.customer = None
        self.assertEqual(self.subscribe(False).status_code, 400)
        self.assertIsNone(self.remote.customer)
        self.assertFalse(Subscription.objects.exists())

    def test_checkbox_must_be_json_true_and_known_consent_version(self):
        for consent in ('true', 1, 'yes'):
            self.assertEqual(self.subscribe(consent).status_code, 400)
        self.assertEqual(self.subscribe(consent_version='old-copy').status_code, 400)
        self.assertFalse(Subscription.objects.exists())

    def test_explicit_request_activates_without_smtp_or_confirmation_email(self):
        self.assertEqual(self.subscribe().status_code, 200)
        sub = Subscription.objects.get()
        self.assertEqual(sub.status, Subscription.ACTIVE)
        self.assertEqual(sub.provider, 'messaging')
        self.assertIsNotNone(sub.marketing_consented_at)
        self.assertFalse(Email.objects.exists())

    def test_new_customer_opt_in_is_recorded_only_on_explicit_request(self):
        self.remote.customer = None
        self.assertEqual(self.subscribe().status_code, 200)
        self.assertEqual(self.remote.customer['defaultEmailAddress']['marketingState'], 'SUBSCRIBED')
        self.assertEqual(self.remote.consent_writes[0]['emailMarketingConsent']['marketingOptInLevel'], 'SINGLE_OPT_IN')

    def test_dispatch_is_not_sent_until_flow_receipt_and_keeps_other_tags(self):
        self.subscribe()
        self.make_available()
        first = services.process_notifications()
        job = Email.objects.get(kind=Email.RESTOCK)
        self.assertEqual(job.status, 'dispatched')
        self.assertEqual(first['sent'], 0)
        self.assertIn('scentory-restock-ready|test-perfume|12345', self.remote.customer['tags'])
        services.process_notifications()
        self.assertEqual(len(self.remote.added_batches), 1)
        self.remote.complete_flow()
        services.process_notifications()
        job.refresh_from_db()
        self.assertEqual(job.status, Email.SENT)
        self.assertEqual(Subscription.objects.get().status, Subscription.NOTIFIED)
        self.assertEqual(self.remote.customer['tags'], ['VIP'])

    def test_other_capacity_does_not_create_dispatch(self):
        self.subscribe()
        self.stock.return_value = {**VARIANT, 'id': 'gid://shopify/ProductVariant/555', 'available': True}
        services.process_notifications()
        self.assertEqual(self.remote.added_batches, [])

    def test_two_products_same_email_wait_for_first_receipt(self):
        self.subscribe()
        second = Subscription.objects.create(email='customer@example.com', variant_id='gid://shopify/ProductVariant/222',
            sku='second', product_title='Second', variant_title='5ml', product_handle='second',
            provider='messaging', shopify_customer_id='gid://shopify/Customer/42',
            marketing_consented_at=timezone.now(), status=Subscription.ACTIVE)
        self.stock.side_effect = lambda sku, **kwargs: ({**VARIANT, 'available': True} if sku == VARIANT['sku'] else
            {**VARIANT, 'id': second.variant_id, 'sku': 'second', 'handle': 'second', 'available': True})
        services.process_notifications()
        services.process_notifications()
        self.assertEqual(len(self.remote.added_batches), 1)
        self.assertNotIn('scentory-restock-ready|second|222', self.remote.customer['tags'])
        self.remote.complete_flow()
        Email.objects.filter(status=Email.PENDING).update(available_at=timezone.now())
        services.process_notifications()
        self.assertEqual(len(self.remote.added_batches), 2)
        self.assertIn('scentory-restock-ready|second|222', self.remote.customer['tags'])
        self.assertNotIn('scentory-restock-ready|test-perfume|12345', self.remote.customer['tags'])

    def test_marketing_opt_out_prevents_dispatch_and_worker_never_resubscribes(self):
        self.subscribe()
        self.remote.customer['defaultEmailAddress']['marketingState'] = 'UNSUBSCRIBED'
        self.make_available()
        services.process_notifications()
        self.assertEqual(self.remote.added_batches, [])
        self.assertEqual(self.remote.consent_writes, [])
        self.assertEqual(Subscription.objects.get().status, Subscription.CANCELLED)

    def test_local_cancellation_before_dispatch_prevents_sending(self):
        self.subscribe()
        sub = Subscription.objects.get()
        self.client.get(f'/api/restock/unsubscribe/{sub.unsubscribe_token}/')
        self.make_available()
        services.process_notifications()
        self.assertEqual(self.remote.added_batches, [])

    def test_ambiguous_submission_is_blocked_and_never_retriggered(self):
        self.subscribe()
        self.make_available()
        self.remote.fail_add = True
        services.process_notifications()
        self.remote.fail_add = False
        Email.objects.update(available_at=timezone.now(), lease_until=timezone.now()-timedelta(minutes=10))
        services.process_notifications()
        self.assertEqual(len(self.remote.added_batches), 1)
        self.assertEqual(Email.objects.get().status, Email.FAILED)
        self.assertNotEqual(Subscription.objects.get().status, Subscription.NOTIFIED)

    def test_status_exposes_consent_requirement_without_smtp_host(self):
        data = self.client.get('/api/restock/status/').json()
        self.assertTrue(data['enabled'])
        self.assertEqual(data['provider'], 'messaging')
        self.assertTrue(data['requires_marketing_consent'])

    def test_old_interrupted_dispatch_is_blocked_even_with_frequent_polling(self):
        self.subscribe()
        sub = Subscription.objects.get()
        job = Email.objects.create(subscription=sub, kind=Email.RESTOCK, provider='messaging', status=Email.PROCESSING)
        slot = MessagingSlot.objects.create(email=sub.email, job=job, state='preparing',
            customer_id=sub.shopify_customer_id, started_at=timezone.now()-timedelta(minutes=11))
        services.process_notifications()
        slot.refresh_from_db()
        self.assertEqual(slot.state, 'blocked')
        self.assertEqual(self.remote.added_batches, [])

    def test_receipt_without_job_tag_does_not_erase_another_worker_lease(self):
        self.subscribe()
        self.make_available()
        services.process_notifications()
        slot = MessagingSlot.objects.get()
        MessagingSlot.objects.filter(pk=slot.pk).update(reconcile_until=timezone.now()+timedelta(minutes=2))
        self.remote.complete_flow()
        services.process_notifications()
        self.assertEqual(Email.objects.get().status, 'dispatched')

    def test_global_opt_out_while_waiting_cancels_without_false_sent_status(self):
        self.subscribe()
        self.make_available()
        services.process_notifications()
        self.remote.customer['defaultEmailAddress']['marketingState'] = 'UNSUBSCRIBED'
        services.process_notifications()
        self.assertEqual(Email.objects.get().status, Email.SKIPPED)
        self.assertEqual(Subscription.objects.get().status, Subscription.CANCELLED)
        self.assertIn('scentory-restock-ready|test-perfume|12345', self.remote.customer['tags'])
        self.assertIsNotNone(MessagingSlot.objects.get().job_id)
        self.remote.complete_flow()
        services.process_notifications()
        self.assertEqual(self.remote.customer['tags'], ['VIP'])

    @override_settings(RESTOCK_MESSAGING_FLOW_READY=False)
    def test_unconfigured_flow_cannot_accept_subscriptions(self):
        self.assertEqual(self.subscribe().status_code, 503)

    def test_legacy_smtp_requests_are_not_automatically_enrolled_or_sent(self):
        Subscription.objects.create(email='legacy@example.com', variant_id=VARIANT['id'], sku=VARIANT['sku'],
            product_title='Legacy', variant_title='100ml', product_handle='test-perfume', status=Subscription.ACTIVE)
        self.make_available()
        services.process_notifications()
        self.assertEqual(self.remote.added_batches, [])
        self.assertFalse(Email.objects.exists())

    @override_settings(RESTOCK_NOTIFICATIONS_ENABLED=False, RESTOCK_MESSAGING_FLOW_READY=False)
    def test_preflight_checks_server_token_scopes_without_sending_or_enabling(self):
        output = StringIO()
        call_command('check_restock_messaging', stdout=output)
        self.assertIn('permissions verified', output.getvalue())
        self.assertEqual(self.remote.added_batches, [])
        self.assertFalse(Subscription.objects.exists())

    def test_preflight_rejects_missing_customer_write_permission(self):
        self.remote.scopes = ['read_products']
        with self.assertRaisesMessage(CommandError, 'write_customers'):
            call_command('check_restock_messaging', stdout=StringIO())

    def test_watch_command_dispatches_then_stops_cleanly_on_interrupt(self):
        self.subscribe()
        self.make_available()
        output = StringIO()
        with mock.patch('time.sleep', side_effect=KeyboardInterrupt):
            call_command('process_restock_notifications', watch=True, interval=60, stdout=output)
        self.assertEqual(Email.objects.get().status, 'dispatched')
        self.assertIn('Worker stopped', output.getvalue())

    def test_late_receipt_retry_cannot_acknowledge_the_next_product(self):
        self.subscribe()
        self.make_available()
        services.process_notifications()
        old_tag = next(t for t in self.remote.customer['tags'] if t.startswith('scentory-restock-job|'))
        self.remote.complete_flow(old_tag)
        services.process_notifications()
        second = Subscription.objects.create(email='customer@example.com', variant_id='gid://shopify/ProductVariant/222',
            sku='second', product_title='Second', variant_title='5ml', product_handle='second',
            provider='messaging', shopify_customer_id='gid://shopify/Customer/42',
            marketing_consented_at=timezone.now(), status=Subscription.ACTIVE)
        self.stock.return_value = {**VARIANT, 'id':second.variant_id, 'sku':'second', 'available':True}
        services.process_notifications()
        self.remote.complete_flow(old_tag)
        services.process_notifications()
        self.assertEqual(Email.objects.get(subscription=second).status, 'dispatched')
        self.assertIsNotNone(MessagingSlot.objects.get().job_id)

    def test_previous_cleanup_between_job_selection_and_slot_acquisition_uses_fresh_tags(self):
        from .messaging import reconcile_receipts, dispatch
        self.subscribe()
        self.make_available()
        services.process_notifications()
        self.remote.complete_flow()
        second = Subscription.objects.create(email='customer@example.com', variant_id='gid://shopify/ProductVariant/222',
            sku='second', product_title='Second', variant_title='5ml', product_handle='second',
            provider='messaging', shopify_customer_id='gid://shopify/Customer/42',
            marketing_consented_at=timezone.now(), status=Subscription.QUEUED)
        job = Email.objects.create(subscription=second,kind=Email.RESTOCK,provider='messaging',status=Email.PROCESSING)
        original = MessagingSlot.objects.get_or_create
        def acquire_after_previous_cleanup(*args, **kwargs):
            reconcile_receipts()
            return original(*args, **kwargs)
        with mock.patch.object(MessagingSlot.objects,'get_or_create',side_effect=acquire_after_previous_cleanup):
            dispatch(job)
        job.refresh_from_db()
        self.assertEqual(job.status, 'dispatched')
