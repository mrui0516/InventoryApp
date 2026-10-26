import logging
import threading
import uuid
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, transaction
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.utils import timezone

from stock.services.shopify_client import ShopifyClient
from .models import RestockSubscription as Subscription, RestockEmail as Email

logger = logging.getLogger(__name__)
_worker_lock = threading.Lock()

VARIANT_QUERY = '''query RestockVariant($query:String!){
  productVariants(first:10,query:$query){
    nodes{id sku title inventoryQuantity inventoryPolicy product{id title handle status onlineStoreUrl}}
    pageInfo{hasNextPage endCursor}
  }
}'''


def enabled():
    if not getattr(settings,'RESTOCK_NOTIFICATIONS_ENABLED',False):
        return False
    if provider() == 'messaging':
        from .messaging import configured
        return configured()
    if provider() != 'smtp':
        return False
    backend = settings.EMAIL_BACKEND
    if getattr(settings,'TESTING',False) and backend.endswith('.locmem.EmailBackend'):
        return True
    return backend.endswith('.smtp.EmailBackend') and bool(settings.EMAIL_HOST)


def provider():
    return getattr(settings, 'RESTOCK_PROVIDER', 'smtp')


def lookup_variant(sku, client=None):
    client = client or ShopifyClient()
    escaped = sku.replace('\\','\\\\').replace('"','\\"')
    data = client.graphql(VARIANT_QUERY, {'query':f'sku:"{escaped}"'})
    matches = [v for v in data.get('productVariants',{}).get('nodes',[]) if v.get('sku')==sku]
    if len(matches)!=1:
        return None
    variant = matches[0]
    product = variant['product']
    if product['status']!='ACTIVE' or not product.get('onlineStoreUrl'):
        return None
    quantity = variant.get('inventoryQuantity') or 0
    return {'id':variant['id'],'sku':sku,'title':variant['title'],
            'quantity':quantity,'available':quantity>0 or variant['inventoryPolicy']=='CONTINUE',
            'product_id':product['id'],'product_title':product['title'],'handle':product['handle']}


def product_url(subscription):
    variant_id = subscription.variant_id.rsplit('/',1)[-1]
    return 'https://www.scentory.pt/products/'+subscription.product_handle+'?'+urlencode({'variant':variant_id})


def register(email, variant, customer_id='', consent_at=None):
    now = timezone.now()
    selected = provider()
    if selected == 'messaging' and (not customer_id or not consent_at):
        raise ValueError('ExplicitMarketingConsentRequired')
    with transaction.atomic():
        sub, created = Subscription.objects.select_for_update().get_or_create(
            email=email.strip().lower(), variant_id=variant['id'], defaults={
                'sku':variant['sku'],'product_title':variant['product_title'],
                'variant_title':variant['title'],'product_handle':variant['handle']})
        if not created and sub.provider == selected and sub.status in (Subscription.ACTIVE,Subscription.QUEUED):
            return sub
        if not created:
            if sub.provider == selected and sub.status==Subscription.PENDING and sub.requested_at>now-timedelta(minutes=10):
                return sub
            sub.generation += 1
            sub.confirm_token = uuid.uuid4()
            sub.status = Subscription.PENDING
            sub.created_at = now
            sub.requested_at = now
            sub.confirmed_at = sub.notified_at = None
            sub.save()
        sub.provider = selected
        if selected == 'messaging':
            sub.shopify_customer_id = customer_id
            sub.marketing_consented_at = consent_at
            sub.marketing_consent_version = 'restock-marketing-v1'
            sub.status = Subscription.ACTIVE
            sub.confirmed_at = now
        sub.save()
        if selected == 'smtp':
            Email.objects.get_or_create(subscription=sub,kind=Email.CONFIRM,generation=sub.generation,
                                        defaults={'provider': selected})
    return sub


def _context(sub):
    base = settings.RESTOCK_PUBLIC_URL.rstrip('/')+'/'
    return {'subscription':sub,'product_url':product_url(sub),
            'confirm_url':base+f'confirm/{sub.confirm_token}/',
            'unsubscribe_url':base+f'unsubscribe/{sub.unsubscribe_token}/'}


def process_notifications(limit=100):
    counts = {'queued':0,'sent':0,'failed':0}
    if not enabled():
        return counts
    selected = provider()
    if selected == 'messaging':
        from .messaging import reconcile_receipts
        counts['sent'] += reconcile_receipts(limit)
    stocks = {}

    def stock(sku, fresh=False):
        if fresh or sku not in stocks:
            try:
                stocks[sku] = lookup_variant(sku)
            except Exception as exc:
                logger.warning('Restock stock lookup failed: %s',type(exc).__name__)
                stocks[sku] = None
        return stocks[sku]

    for sub in Subscription.objects.filter(status=Subscription.ACTIVE,provider=selected).order_by(F('checked_at').asc(nulls_first=True),'pk')[:limit]:
        variant = stock(sub.sku)
        Subscription.objects.filter(pk=sub.pk,status=Subscription.ACTIVE).update(checked_at=timezone.now())
        if not variant or variant['id']!=sub.variant_id or not variant['available']:
            continue
        with transaction.atomic():
            if not Subscription.objects.filter(pk=sub.pk,status=Subscription.ACTIVE).update(status=Subscription.QUEUED):
                continue
            Email.objects.get_or_create(subscription=sub,kind=Email.RESTOCK,generation=sub.generation,
                                        defaults={'provider': selected})
            counts['queued'] += 1

    now = timezone.now()
    eligible = Q(status=Email.PENDING) | Q(status=Email.PROCESSING,lease_until__lt=now)
    jobs = list(Email.objects.filter(eligible,provider=selected,available_at__lte=now,attempts__lt=6).order_by('available_at','pk').values_list('pk',flat=True)[:limit])
    for pk in jobs:
        claimed_at = timezone.now()
        if not Email.objects.filter(eligible,pk=pk).update(status=Email.PROCESSING,lease_until=claimed_at+timedelta(minutes=5),attempts=F('attempts')+1):
            continue
        job = Email.objects.select_related('subscription').get(pk=pk)
        sub = job.subscription
        required = Subscription.PENDING if job.kind==Email.CONFIRM else Subscription.QUEUED
        expired_confirmation = job.kind==Email.CONFIRM and sub.created_at<timezone.now()-timedelta(days=7)
        if sub.status!=required or job.generation!=sub.generation or expired_confirmation:
            Email.objects.filter(pk=pk).update(status=Email.SKIPPED,lease_until=None)
            continue
        if job.kind==Email.RESTOCK:
            variant = stock(sub.sku,fresh=True)
            if not variant or variant['id']!=sub.variant_id or not variant['available']:
                Email.objects.filter(pk=pk).update(status=Email.PENDING,attempts=F('attempts')-1,
                                                  available_at=now+timedelta(minutes=1),lease_until=None)
                continue
        if selected == 'messaging':
            from .messaging import dispatch
            try:
                dispatch(job)
            except Exception as exc:
                # Failures before a remote submission are retryable. The bridge
                # itself blocks ambiguous writes so they never enter this retry.
                Email.objects.filter(pk=pk,status=Email.PROCESSING).update(
                    status=Email.FAILED if job.attempts>=6 else Email.PENDING,
                    last_error=type(exc).__name__,available_at=now+timedelta(minutes=min(60,2**job.attempts)),lease_until=None)
                counts['failed'] += 1
            continue
        context = _context(sub)
        context['confirmation'] = job.kind==Email.CONFIRM
        title = sub.product_title.replace('\r',' ').replace('\n',' ')
        subject = ('Confirme o aviso de disponibilidade — ' if context['confirmation'] else 'De volta à Scentory — ')+title
        try:
            message = EmailMultiAlternatives(subject,
                render_to_string('restock_alerts/email.txt',context),
                settings.DEFAULT_FROM_EMAIL,[sub.email])
            message.attach_alternative(render_to_string('restock_alerts/email.html',context),'text/html')
            if message.send(fail_silently=False)!=1:
                raise RuntimeError('EmailNotAccepted')
        except Exception as exc:
            Email.objects.filter(pk=pk).update(status=Email.FAILED if job.attempts>=6 else Email.PENDING,
                last_error=type(exc).__name__,available_at=now+timedelta(minutes=min(60,2**job.attempts)),lease_until=None)
            counts['failed'] += 1
            continue
        with transaction.atomic():
            Email.objects.filter(pk=pk).update(status=Email.SENT,sent_at=timezone.now(),lease_until=None,last_error='')
            if job.kind==Email.RESTOCK:
                Subscription.objects.filter(pk=sub.pk,status=Subscription.QUEUED,generation=job.generation).update(
                    status=Subscription.NOTIFIED,notified_at=timezone.now())
        counts['sent'] += 1
    return counts


def wake_worker():
    if not enabled() or not getattr(settings,'RESTOCK_BACKGROUND',True):
        return

    def run():
        if not _worker_lock.acquire(blocking=False):
            return
        try:
            close_old_connections()
            process_notifications()
        except Exception as exc:
            logger.warning('Restock background worker failed: %s',type(exc).__name__)
        finally:
            close_old_connections()
            _worker_lock.release()
    try:
        threading.Thread(target=run,daemon=True,name='restock-email').start()
    except RuntimeError:
        logger.warning('Restock thread unavailable; queued messages await reconciliation')
