import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import RestockSubscription, RestockEmail
from .services import process_notifications


VARIANT = {'id':'gid://shopify/ProductVariant/12345','sku':'6290000000001',
           'title':'100ml','quantity':0,'available':False,
           'product_id':'gid://shopify/Product/123','product_title':'Test Perfume',
           'handle':'test-perfume'}


@override_settings(TESTING=True, RESTOCK_NOTIFICATIONS_ENABLED=True,
                   RESTOCK_BACKGROUND=False,
                   RESTOCK_ALLOWED_ORIGINS=['https://www.scentory.pt'],
                   RESTOCK_PUBLIC_URL='https://gestao.scentory.pt/api/restock/',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='Scentory <info@scentory.pt>')
class RestockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.lookup = mock.patch('restock_alerts.services.lookup_variant', return_value=dict(VARIANT)).start()
        self.addCleanup(mock.patch.stopall)

    def subscribe(self, email='Customer@example.com', sku=VARIANT['sku'], variant_id='12345', origin='https://www.scentory.pt'):
        return self.client.post('/api/restock/subscribe/', data=json.dumps({
            'email':email,'sku':sku,'variant_id':variant_id}),
            content_type='application/json', HTTP_ORIGIN=origin)

    def activate(self):
        self.subscribe()
        sub=RestockSubscription.objects.get()
        self.client.get(f'/api/restock/confirm/{sub.confirm_token}/')
        sub.refresh_from_db()
        return sub

    def test_confirmation_required_then_correct_capacity_is_notified_once(self):
        response=self.subscribe()
        self.assertEqual(response.status_code,200)
        sub=RestockSubscription.objects.get()
        self.assertEqual(sub.email,'customer@example.com')
        self.assertEqual(sub.status,RestockSubscription.PENDING)
        process_notifications()
        self.assertEqual(len(mail.outbox),1)
        self.assertIn(str(sub.confirm_token),mail.outbox[0].body)
        self.assertIn('100ml',mail.outbox[0].body)
        self.client.get(f'/api/restock/confirm/{sub.confirm_token}/')
        process_notifications()
        self.assertEqual(len(mail.outbox),1)
        self.lookup.return_value={**VARIANT,'quantity':1,'available':True}
        process_notifications()
        process_notifications()
        self.assertEqual(len(mail.outbox),2)
        self.assertIn('?variant=12345',mail.outbox[1].body)
        sub.refresh_from_db()
        self.assertEqual(sub.status,RestockSubscription.NOTIFIED)

    def test_duplicate_and_case_variant_email_do_not_duplicate_records_or_messages(self):
        self.subscribe()
        self.subscribe('CUSTOMER@example.com')
        self.assertEqual(RestockSubscription.objects.count(),1)
        self.assertEqual(RestockEmail.objects.count(),1)

    def test_available_variant_and_wrong_variant_id_are_rejected(self):
        self.lookup.return_value={**VARIANT,'quantity':2,'available':True}
        self.assertEqual(self.subscribe().status_code,409)
        self.lookup.return_value=dict(VARIANT)
        self.assertEqual(self.subscribe(variant_id='99999').status_code,400)
        self.assertFalse(RestockSubscription.objects.exists())

    def test_other_capacity_restock_does_not_trigger_this_subscription(self):
        sub=self.activate()
        self.lookup.side_effect=lambda sku, **kwargs: dict(VARIANT) if sku==VARIANT['sku'] else {**VARIANT,'quantity':10,'available':True}
        process_notifications()
        sub.refresh_from_db()
        self.assertEqual(sub.status,RestockSubscription.ACTIVE)
        self.assertFalse(RestockEmail.objects.filter(kind=RestockEmail.RESTOCK).exists())

    def test_cancellation_prevents_queued_notification(self):
        sub=self.activate()
        self.client.get(f'/api/restock/unsubscribe/{sub.unsubscribe_token}/')
        self.lookup.return_value={**VARIANT,'quantity':2,'available':True}
        process_notifications()
        sub.refresh_from_db()
        self.assertEqual(sub.status,RestockSubscription.CANCELLED)
        self.assertFalse(RestockEmail.objects.filter(kind=RestockEmail.RESTOCK,status=RestockEmail.SENT).exists())

    def test_failed_smtp_is_retryable_and_never_marks_subscriber_notified(self):
        sub=self.activate()
        self.lookup.return_value={**VARIANT,'quantity':2,'available':True}
        with mock.patch('restock_alerts.services.EmailMultiAlternatives.send',side_effect=OSError('offline')):
            process_notifications()
        job=RestockEmail.objects.get(kind=RestockEmail.RESTOCK)
        self.assertEqual(job.status,RestockEmail.PENDING)
        self.assertEqual(job.attempts,1)
        self.assertGreater(job.available_at,timezone.now())
        sub.refresh_from_db()
        self.assertNotEqual(sub.status,RestockSubscription.NOTIFIED)

    def test_unconfirmed_subscription_is_not_notified(self):
        self.subscribe()
        self.lookup.return_value={**VARIANT,'quantity':5,'available':True}
        process_notifications()
        self.assertFalse(RestockEmail.objects.filter(kind=RestockEmail.RESTOCK).exists())

    def test_cors_and_input_validation(self):
        self.assertEqual(self.subscribe(origin='https://evil.example').status_code,403)
        self.assertEqual(self.subscribe(email='not-an-email').status_code,400)
        response=self.client.options('/api/restock/subscribe/',HTTP_ORIGIN='https://www.scentory.pt')
        self.assertEqual(response['Access-Control-Allow-Origin'],'https://www.scentory.pt')
        self.assertFalse(RestockSubscription.objects.exists())

    @override_settings(RESTOCK_NOTIFICATIONS_ENABLED=False)
    def test_disabled_service_does_not_accept_subscriptions(self):
        self.assertEqual(self.subscribe().status_code,503)
        self.assertFalse(RestockSubscription.objects.exists())

    def test_demand_is_private_and_aggregates_by_variant(self):
        self.activate()
        self.assertEqual(self.client.get('/api/restock/demand/').status_code,302)
        manager=get_user_model().objects.create_user('manager',password='test',is_staff=True)
        self.client.force_login(manager)
        response=self.client.get('/api/restock/demand/')
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Test Perfume')
        self.assertNotContains(response,'customer@example.com')

    def test_deleted_or_reassigned_variant_is_never_notified(self):
        sub=self.activate()
        self.lookup.return_value={**VARIANT,'id':'gid://shopify/ProductVariant/98765','quantity':3,'available':True}
        process_notifications()
        sub.refresh_from_db()
        self.assertEqual(sub.status,RestockSubscription.ACTIVE)

    def test_expired_confirmation_cannot_activate_subscription(self):
        self.subscribe()
        sub=RestockSubscription.objects.get()
        RestockSubscription.objects.filter(pk=sub.pk).update(created_at=timezone.now()-timedelta(days=8))
        self.client.get(f'/api/restock/confirm/{sub.confirm_token}/')
        sub.refresh_from_db()
        self.assertEqual(sub.status,RestockSubscription.PENDING)

    def test_old_confirmation_link_does_not_claim_cancelled_request_is_active(self):
        sub=self.activate()
        self.client.get(f'/api/restock/unsubscribe/{sub.unsubscribe_token}/')
        response=self.client.get(f'/api/restock/confirm/{sub.confirm_token}/')
        self.assertNotContains(response,'Pedido confirmado')

    def test_batch_limit_does_not_starve_later_products(self):
        first=self.activate()
        second=RestockSubscription.objects.create(email='second@example.com',variant_id='gid://shopify/ProductVariant/67890',
            sku='second',product_title='Second perfume',variant_title='100ml',product_handle='second',status=RestockSubscription.ACTIVE)
        self.lookup.side_effect=lambda sku,**kwargs: dict(VARIANT) if sku==first.sku else {
            **VARIANT,'id':second.variant_id,'sku':second.sku,'quantity':2,'available':True}
        process_notifications(limit=1)
        process_notifications(limit=1)
        self.assertTrue(RestockEmail.objects.filter(subscription=second,kind=RestockEmail.RESTOCK).exists())

    def test_zero_backend_send_count_is_not_success(self):
        self.subscribe()
        with mock.patch('restock_alerts.services.EmailMultiAlternatives.send',return_value=0):
            process_notifications()
        self.assertFalse(RestockEmail.objects.filter(status=RestockEmail.SENT).exists())
