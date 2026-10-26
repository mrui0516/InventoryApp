"""Shopify Messaging bridge. No arbitrary-email API or HTTP Flow action required."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from stock.services.shopify_client import ShopifyClient, ShopifyError
from .models import MessagingSlot as Slot, RestockEmail as Email, RestockSubscription as Subscription

CUSTOMER_FIELDS = 'id tags defaultEmailAddress{emailAddress marketingState marketingUpdatedAt}'
CUSTOMERS_QUERY = '''query RestockCustomers($query:String!){customers(first:2,query:$query){
 nodes{''' + CUSTOMER_FIELDS + '''} pageInfo{hasNextPage endCursor}}}'''
CUSTOMER_QUERY = 'query RestockCustomer($id:ID!){customer(id:$id){' + CUSTOMER_FIELDS + '}}'
CREATE_MUTATION = '''mutation RestockCustomerCreate($input:CustomerInput!){customerCreate(input:$input){
 customer{id} userErrors{field message}}}'''
CONSENT_MUTATION = '''mutation RestockConsent($input:CustomerEmailMarketingConsentUpdateInput!){
 customerEmailMarketingConsentUpdate(input:$input){customer{id} userErrors{field message}}}'''
ADD_MUTATION = '''mutation RestockTagsAdd($id:ID!,$tags:[String!]!){tagsAdd(id:$id,tags:$tags){
 node{id} userErrors{field message}}}'''
REMOVE_MUTATION = '''mutation RestockTagsRemove($id:ID!,$tags:[String!]!){tagsRemove(id:$id,tags:$tags){
 node{id} userErrors{field message}}}'''
SCOPES_QUERY = 'query RestockMessagingScopes{currentAppInstallation{accessScopes{handle}}}'


def client():
    return ShopifyClient(api_version=getattr(settings, 'RESTOCK_MESSAGING_API_VERSION', '2026-10'), timeout=20)


def configured():
    return bool(getattr(settings, 'RESTOCK_MESSAGING_FLOW_READY', False) and client().is_configured())


def _mutation(api, query, variables, field):
    payload = api.graphql(query, variables).get(field) or {}
    if payload.get('userErrors') or not (payload.get('node') or payload.get('customer')):
        # Avoid retaining external error messages that may contain customer data.
        raise ShopifyError('MessagingMutationRejected')
    return payload


def consenting_customer(email, consent_at):
    """Only called for a foreground request with explicit consent, never a worker."""
    api = client()
    escaped = email.replace('\\', '\\\\').replace('"', '\\"')
    result = api.graphql(CUSTOMERS_QUERY, {'query': f'email:"{escaped}"'}).get('customers') or {}
    matches = [c for c in result.get('nodes', [])
               if (c.get('defaultEmailAddress') or {}).get('emailAddress', '').lower() == email.lower()]
    if result.get('pageInfo', {}).get('hasNextPage') or len(matches) > 1:
        raise ShopifyError('AmbiguousCustomer')
    consent = {'marketingState': 'SUBSCRIBED', 'marketingOptInLevel': 'SINGLE_OPT_IN',
               'consentUpdatedAt': consent_at.isoformat()}
    if not matches:
        payload = _mutation(api, CREATE_MUTATION, {'input': {'email': email, 'emailMarketingConsent': consent}}, 'customerCreate')
        return payload['customer']['id']
    customer = matches[0]
    address = customer['defaultEmailAddress']
    if address['marketingState'] == 'SUBSCRIBED':
        return customer['id']
    updated = parse_datetime(address.get('marketingUpdatedAt') or '')
    if address['marketingState'] in ('INVALID', 'REDACTED') or (updated and updated > consent_at):
        raise ShopifyError('MarketingConsentChanged')
    _mutation(api, CONSENT_MUTATION, {'input': {'customerId': customer['id'], 'emailMarketingConsent': consent}},
              'customerEmailMarketingConsentUpdate')
    return customer['id']


def _eligible_customer(api, customer_id, email):
    customer = api.graphql(CUSTOMER_QUERY, {'id': customer_id}).get('customer')
    address = (customer or {}).get('defaultEmailAddress') or {}
    if address.get('emailAddress', '').lower() != email.lower() or address.get('marketingState') != 'SUBSCRIBED':
        return None
    return customer


def _block(slot, job, reason):
    Slot.objects.filter(pk=slot.pk,job_id=job.pk).update(state='blocked',checked_at=timezone.now())
    Email.objects.filter(pk=job.pk).update(status=Email.FAILED,last_error=reason,lease_until=None)


def dispatch(job):
    sub = job.subscription
    api = client()
    slot, _ = Slot.objects.get_or_create(email=sub.email)
    if slot.job_id:
        if slot.job_id == job.pk:
            _block(slot,job,'AmbiguousPreviousDispatch')
        else:
            Email.objects.filter(pk=job.pk).update(status=Email.PENDING,attempts=max(0,job.attempts-1),
                available_at=timezone.now()+timedelta(seconds=30),lease_until=None)
        return
    token = uuid.uuid4()
    if not Slot.objects.filter(pk=slot.pk,job__isnull=True).update(job=job,token=token,
            state='preparing',customer_id=sub.shopify_customer_id,checked_at=timezone.now(),started_at=timezone.now(),reconcile_until=None):
        Email.objects.filter(pk=job.pk).update(status=Email.PENDING,attempts=max(0,job.attempts-1),
            available_at=timezone.now()+timedelta(seconds=30),lease_until=None)
        return
    slot.refresh_from_db()
    try:
        # Read after taking the slot: an earlier context may have been cleaned
        # by another worker between our job claim and this slot acquisition.
        customer = _eligible_customer(api, sub.shopify_customer_id, sub.email)
    except Exception:
        Slot.objects.filter(pk=slot.pk,job_id=job.pk,token=slot.token).update(job=None,state='idle')
        raise
    if not customer:
        Subscription.objects.filter(email=sub.email,provider='messaging',status__in=[Subscription.ACTIVE,Subscription.QUEUED]).update(status=Subscription.CANCELLED)
        Email.objects.filter(pk=job.pk).update(status=Email.SKIPPED,last_error='MarketingOptOut',lease_until=None)
        Slot.objects.filter(pk=slot.pk,job_id=job.pk,token=slot.token).update(job=None,state='idle')
        return
    # A stale tag might still be referenced by an earlier Flow execution. Do not
    # replace its context or remove it blindly; an operator must inspect it.
    if any(t.lower().startswith(('scentory-restock-ready|','scentory-restock-job|','scentory-restock-cancel|')) for t in customer['tags']):
        _block(slot,job,'ExistingMessagingContext')
        return
    sub.refresh_from_db()
    if sub.status != Subscription.QUEUED or sub.generation != job.generation:
        Slot.objects.filter(pk=slot.pk,job_id=job.pk).update(job=None,state='idle')
        Email.objects.filter(pk=job.pk).update(status=Email.SKIPPED,lease_until=None)
        return
    try:
        _mutation(api, ADD_MUTATION, {'id': slot.customer_id, 'tags': [slot.ready_tag,slot.cancel_tag,slot.job_tag]}, 'tagsAdd')
    except Exception:
        # Shopify may already have accepted the tags and sent the email. Never
        # remove/re-add the trigger or assume that retrying is harmless.
        _block(slot,job,'AmbiguousTagSubmission')
        return
    with transaction.atomic():
        Slot.objects.filter(pk=slot.pk,job_id=job.pk).update(state='waiting',checked_at=timezone.now())
        Email.objects.filter(pk=job.pk).update(status=Email.DISPATCHED,lease_until=None,last_error='')


def reconcile_receipts(limit=100):
    accepted = 0
    api = client()
    slots = Slot.objects.exclude(job=None).exclude(state='blocked').select_related('job__subscription').order_by('checked_at','pk')[:limit]
    for slot in slots:
        now = timezone.now()
        identity = Slot.objects.filter(pk=slot.pk,job_id=slot.job_id,token=slot.token)
        if not identity.filter(Q(reconcile_until__isnull=True)|Q(reconcile_until__lte=now)).update(
                reconcile_until=now+timedelta(minutes=2),checked_at=now):
            continue
        try:
            accepted += _reconcile_one(api,slot,identity)
        except Exception:
            continue
        finally:
            identity.update(reconcile_until=None)
    return accepted


def _reconcile_one(api, slot, identity):
    job, sub = slot.job, slot.job.subscription
    if slot.state == 'preparing':
        if slot.started_at < timezone.now()-timedelta(minutes=10):
            _block(slot,job,'InterruptedDispatch')
        return 0
    customer = api.graphql(CUSTOMER_QUERY, {'id':slot.customer_id}).get('customer')
    address = (customer or {}).get('defaultEmailAddress') or {}
    if address.get('emailAddress','').lower() != sub.email.lower():
        _block(slot,job,'CustomerEmailChanged')
        return 0
    opted_out = address.get('marketingState') != 'SUBSCRIBED'
    tags = {t.lower() for t in customer['tags']}
    if opted_out:
        Email.objects.filter(pk=job.pk).update(status=Email.SKIPPED,last_error='MarketingOptOut',lease_until=None)
        Subscription.objects.filter(email=sub.email,provider='messaging',status__in=[Subscription.ACTIVE,Subscription.QUEUED]).update(status=Subscription.CANCELLED)
    if slot.job_tag.lower() in tags:
        # Even an opt-out does not prove that an older Flow execution has
        # stopped. Keep its context locked so a later re-opt-in cannot mix mail.
        if slot.started_at < timezone.now()-timedelta(hours=1):
            _block(slot,job,'FlowReceiptTimeout')
        return 0
    if not identity.update(state='cleanup'):
        return 0
    cleanup = [t for t in [slot.ready_tag,slot.cancel_tag,slot.job_tag] if t.lower() in tags]
    if cleanup:
        _mutation(api,REMOVE_MUTATION,{'id':slot.customer_id,'tags':cleanup},'tagsRemove')
    with transaction.atomic():
        if not identity.filter(state='cleanup').update(job=None,state='idle',reconcile_until=None):
            return 0
        if opted_out:
            Email.objects.filter(pk=job.pk).update(status=Email.SKIPPED,last_error='MarketingOptOut',lease_until=None)
            Subscription.objects.filter(email=sub.email,provider='messaging',status__in=[Subscription.ACTIVE,Subscription.QUEUED]).update(status=Subscription.CANCELLED)
            return 0
        Email.objects.filter(pk=job.pk).update(status=Email.SENT,sent_at=timezone.now(),last_error='',lease_until=None)
        Subscription.objects.filter(pk=sub.pk,status=Subscription.QUEUED,generation=job.generation).update(
            status=Subscription.NOTIFIED,notified_at=timezone.now())
    return 1
