import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required,user_passes_test
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Count,Max,Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from stock.permissions import has_manager_access
from . import services
from .models import RestockSubscription as Subscription, MessagingSlot


def _response(data,origin,status=200):
    response = JsonResponse(data,status=status)
    response['Cache-Control'] = 'no-store'
    if origin in settings.RESTOCK_ALLOWED_ORIGINS:
        response['Access-Control-Allow-Origin'] = origin
        response['Access-Control-Allow-Methods'] = 'POST, OPTIONS, GET'
        response['Access-Control-Allow-Headers'] = 'Content-Type'
        patch_vary_headers(response,['Origin'])
    return response


def _limited(value,limit,seconds):
    key = 'restock:'+hashlib.sha256(value.encode()).hexdigest()
    if cache.add(key,1,seconds):
        return False
    try:
        return cache.incr(key)>limit
    except ValueError:
        return False


@csrf_exempt
def subscribe(request):
    origin = request.headers.get('Origin','')
    if origin not in settings.RESTOCK_ALLOWED_ORIGINS:
        return _response({'error':'origin'},origin,403)
    if request.method=='OPTIONS':
        return _response({'ok':True},origin)
    if request.method!='POST':
        return _response({'error':'method'},origin,405)
    if not services.enabled():
        return _response({'error':'unavailable'},origin,503)
    try:
        if len(request.body)>4096:
            raise ValueError()
        data = json.loads(request.body)
        email = str(data.get('email','')).strip().lower()
        validate_email(email)
        sku = str(data.get('sku','')).strip()
        variant_id = str(data.get('variant_id',''))
        if not sku or len(sku)>100 or not variant_id.isdigit() or len(variant_id)>24:
            raise ValueError()
    except (ValueError,TypeError,AttributeError,ValidationError):
        return _response({'error':'invalid'},origin,400)
    if data.get('website'):
        return _response({'ok':True},origin)
    if services.provider() == 'messaging' and (
            data.get('marketing_consent') is not True or data.get('consent_version') != 'restock-marketing-v1'):
        return _response({'error':'marketing_consent_required'},origin,400)
    if _limited('email:'+email,5,3600) or _limited('ip:'+request.META.get('REMOTE_ADDR',''),40,600):
        return _response({'error':'rate_limit'},origin,429)
    try:
        variant = services.lookup_variant(sku)
    except Exception:
        return _response({'error':'unavailable'},origin,503)
    if not variant or variant['id'].rsplit('/',1)[-1]!=variant_id:
        return _response({'error':'invalid_variant'},origin,400)
    if variant['available']:
        return _response({'error':'already_available'},origin,409)
    if services.provider() == 'messaging':
        from .messaging import consenting_customer
        consent_at = timezone.now()
        try:
            customer_id = consenting_customer(email, consent_at)
        except Exception:
            return _response({'error':'unavailable'},origin,503)
        services.register(email,variant,customer_id=customer_id,consent_at=consent_at)
    else:
        services.register(email,variant)
    transaction.on_commit(services.wake_worker)
    return _response({'ok':True,'provider':services.provider()},origin)


@require_GET
def status(request):
    return _response({'enabled':services.enabled(),'provider':services.provider(),
                      'requires_marketing_consent':services.provider() == 'messaging'},request.headers.get('Origin',''))


@require_GET
def confirm(request,token):
    sub = get_object_or_404(Subscription,confirm_token=token)
    expired = sub.created_at<timezone.now()-timedelta(days=7)
    if sub.status==Subscription.PENDING and not expired:
        Subscription.objects.filter(pk=sub.pk,status=Subscription.PENDING).update(status=Subscription.ACTIVE,confirmed_at=timezone.now())
        transaction.on_commit(services.wake_worker)
    sub.refresh_from_db()
    if sub.status==Subscription.CANCELLED:
        message = 'Este aviso foi cancelado. Pode voltar ao produto para fazer um novo pedido.'
    elif sub.status==Subscription.NOTIFIED:
        message = 'O aviso deste perfume já foi enviado. Consulte a sua caixa de entrada.'
    elif expired and sub.status==Subscription.PENDING:
        message = 'Este link expirou. Pode voltar ao produto e pedir um novo aviso.'
    else:
        message = 'Pedido confirmado. Receberá um email quando esta capacidade voltar a estar disponível.'
    return render(request,'restock_alerts/result.html',{'message':message,'product_url':services.product_url(sub)})


@require_GET
def unsubscribe(request,token):
    sub = get_object_or_404(Subscription,unsubscribe_token=token)
    Subscription.objects.filter(pk=sub.pk).update(status=Subscription.CANCELLED)
    return render(request,'restock_alerts/result.html',{'message':'O aviso deste perfume foi cancelado.','product_url':services.product_url(sub)})


@login_required
@user_passes_test(has_manager_access)
def demand(request):
    rows = Subscription.objects.values('variant_id','sku').annotate(
        product_title=Max('product_title'),variant_title=Max('variant_title'),
        waiting=Count('id',filter=Q(status__in=[Subscription.ACTIVE,Subscription.QUEUED])),
        pending=Count('id',filter=Q(status=Subscription.PENDING)),
        notified=Count('id',filter=Q(status=Subscription.NOTIFIED)),
    ).order_by('-waiting','-pending','product_title')
    return render(request,'restock_alerts/demand.html',{'rows':rows,
        'flow_waiting':MessagingSlot.objects.filter(state='waiting').count(),
        'flow_blocked':MessagingSlot.objects.filter(state='blocked').count()})
