"""One leased worker for durable product jobs and storefront reconciliation."""
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.text import slugify

from stock.models import Product, ShopifyCatalogLease, ShopifyCatalogState
from . import shopify_sync
from .shopify_catalog_client import CatalogClient
from .shopify_client import ShopifyError
from .shopify_catalog_queue import enabled
from .shopify_storefront import sync_storefront

logger = logging.getLogger(__name__)
LEASE_SECONDS = 600


def _renew(token):
    updated = ShopifyCatalogLease.objects.filter(name='catalog', token=token).update(
        expires_at=timezone.now()+timedelta(seconds=LEASE_SECONDS))
    if not updated:
        raise ShopifyError('Catalogue worker lease was lost; leave remaining jobs for the next run.')


def _sync_one(state, client):
    p = state.product
    if p.archived_at or (p.category_id and not p.category.sync_to_shopify):
        return 'skipped'
    if not (p.barcode or '').strip():
        raise ShopifyError('Add a barcode before automatic publication.')
    match = client.find_product_by_handle(state.creation_handle) if state.creation_handle else None
    just_created = False
    if not match:
        match = client.find_product_by_sku(p.barcode.strip())
    if not match:
        if not p.default_price or p.default_price <= 0:
            raise ShopifyError('Add a positive retail price before automatic publication.')
        if not p.images.exists() or not shopify_sync._local_image_path(p):
            raise ShopifyError('Upload a usable product photo before automatic publication.')
        if p.product_type.picks_perfume:
            raise ShopifyError('Made-to-order products need a manual storefront listing.')
        if not state.creation_handle:
            handle = (slugify(p.display_name)[:170] or 'product') + '-' + slugify(p.barcode)
            ShopifyCatalogState.objects.filter(pk=state.pk, creation_handle='').update(creation_handle=handle)
            state.refresh_from_db(fields=['creation_handle'])
        code, detail = shopify_sync.create_product_in_shopify(
            p, client=client, status='DRAFT', handle=state.creation_handle)
        if code != shopify_sync.CREATED:
            raise ShopifyError(detail)
        match = client.find_product_by_handle(state.creation_handle)
        if not match:
            raise ShopifyError('Creation returned success; waiting to resolve the reserved handle.')
        just_created = True
    ShopifyCatalogState.objects.filter(pk=state.pk).update(product_gid=match['id'])
    owned_pending = bool(state.creation_handle and not state.published_by_automation)
    if match.get('status') != 'ACTIVE' and not (owned_pending and match.get('status') == 'DRAFT'):
        return 'skipped'
    if owned_pending:
        try:
            p = Product.objects.select_related('category', 'product_type').get(pk=p.pk)
        except Product.DoesNotExist:
            return 'skipped'
        if p.archived_at or (p.category_id and not p.category.sync_to_shopify):
            return 'skipped'
        if not p.default_price or p.default_price <= 0 or not p.images.exists() or not shopify_sync._local_image_path(p):
            raise ShopifyError('The new listing changed or is incomplete; keep its draft queued.')
        if match.get('status') == 'DRAFT' and not just_created:
            code, detail = shopify_sync.create_product_in_shopify(
                p, client=client, status='DRAFT', handle=state.creation_handle, include_image=False)
            if code != shopify_sync.CREATED:
                raise ShopifyError(detail)
            match = client.find_product_by_handle(state.creation_handle)
            if not match:
                raise ShopifyError('Waiting for the owned draft to become readable.')
        current_revision = ShopifyCatalogState.objects.filter(pk=state.pk).values_list('requested_revision', flat=True).first()
        if current_revision != state.requested_revision:
            raise ShopifyError('Product changed during creation; the newer generation will finish publication.')
    kind, vendor = shopify_sync._shopify_product_type(p), shopify_sync._pretty(p.brand)
    if match.get('product_type') != kind or match.get('vendor') != vendor:
        client.update_catalog_identity(match['id'], kind, vendor)
    if (not match.get('has_image') or (owned_pending and match.get('image_failed'))) and shopify_sync._local_image_path(p):
        code, detail = shopify_sync._attach_image(p, client, match, overwrite=bool(match.get('image_failed')), dry_run=False)
        if code == shopify_sync.ERROR:
            raise ShopifyError(detail)
        if owned_pending:
            match = client.find_product_by_handle(state.creation_handle)
    if owned_pending and (not match or not match.get('image_ready')):
        raise ShopifyError('Waiting for Shopify to finish processing the product photo; draft remains queued.')
    options = {'shop_variants': match['variants']} if match.get('variants') else {}
    code, detail = shopify_sync.sync_product_price_inventory(p, client=client, **options)
    if code not in (shopify_sync.INV_UPDATED, shopify_sync.INV_UNCHANGED):
        raise ShopifyError(detail)
    if owned_pending:
        if match.get('status') == 'DRAFT':
            client.set_product_status(match['id'], 'ACTIVE')
        client.publish_online(match['id'])
        ShopifyCatalogState.objects.filter(pk=state.pk).update(published_by_automation=True)
    return 'synced'


def process_catalog(client=None, *, limit=25):
    if not enabled():
        return {'disabled': True, 'busy': False, 'synced': 0, 'skipped': 0, 'failed': 0}
    client = client or CatalogClient()
    client.require_configured()
    token = uuid.uuid4().hex
    ShopifyCatalogLease.objects.get_or_create(name='catalog')
    now = timezone.now()
    claimed = ShopifyCatalogLease.objects.filter(name='catalog', expires_at__lte=now).update(
        token=token, expires_at=now+timedelta(seconds=LEASE_SECONDS))
    result = {'busy': not bool(claimed), 'synced': 0, 'skipped': 0, 'failed': 0}
    if not claimed:
        return result
    previous_heartbeat = client.lease_heartbeat
    client.lease_heartbeat = lambda: _renew(token)
    try:
        states = list(ShopifyCatalogState.objects.filter(
            requested_revision__gt=F('synced_revision'), next_attempt_at__lte=now)
            .select_related('product__category', 'product__product_type')
            .order_by('next_attempt_at', 'pk')[:max(1, limit)])
        for state in states:
            _renew(token)
            revision = state.requested_revision
            try:
                action = _sync_one(state, client)
                _renew(token)
                ShopifyCatalogState.objects.filter(pk=state.pk).update(
                    synced_revision=revision, attempts=0, last_error='')
                result[action] += 1
            except Exception as exc:
                delay = min(3600, 30 * 2 ** min(state.attempts, 7))
                ShopifyCatalogState.objects.filter(pk=state.pk, requested_revision=revision,
                                                    synced_revision__lt=revision).update(
                    attempts=state.attempts+1, last_error=str(exc)[:2000],
                    next_attempt_at=timezone.now()+timedelta(seconds=delay))
                logger.warning('Shopify catalogue job %s remains queued: %s', state.product_id, exc)
                result['failed'] += 1
        _renew(token)
        result['collections'] = sync_storefront(client=client)
        if getattr(settings, 'RESTOCK_NOTIFICATIONS_ENABLED', False):
            from restock_alerts.services import wake_worker
            wake_worker()
        return result
    finally:
        client.lease_heartbeat = previous_heartbeat
        ShopifyCatalogLease.objects.filter(name='catalog', token=token).update(
            token='', expires_at=timezone.now())
