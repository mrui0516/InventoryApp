"""Customer accounts: links, logging in, and who may open the catalogue.

Decisions (2026-11-07): phone + password; the shop sends the link by WhatsApp;
any Revenda customer may be invited; links last 72 hours, a login 14 days.
"""
import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.signals import user_login_failed
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

from ..models import AccountLink, Customer, CustomerAccount
from ..permissions import has_manager_access
from .contacts import international

LINK_HOURS = 72
SESSION_SECONDS = 14 * 24 * 3600
MAX_MISSES = 5
LOCK_SECONDS = 15 * 60
WRONG = 'Telemóvel ou palavra-passe incorretos.'
LOCKED = 'Demasiadas tentativas. Tente de novo dentro de 15 minutos.'
BAD_LINK = 'Este link não é válido ou já expirou. Peça um novo à loja.'


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def phone_key(phone):
    return international(phone)


def account_of(request):
    """The request's customer account, looked up once; None for staff and visitors."""
    if not hasattr(request, '_customer_account'):
        user = getattr(request, 'user', None)
        request._customer_account = (CustomerAccount.objects.select_related('customer', 'user')
                                     .filter(user=user).first() if user and user.is_authenticated else None)
    return request._customer_account


def may_enter(account):
    user = account.user
    return (account.status == CustomerAccount.ACTIVE and account.customer.kind == Customer.REVENDA
            and user.is_active and not user.is_staff and not user.is_superuser)


def catalog_access(view):
    """The catalogue's pages: a customer with an open account, or as before the
    shop's managers (login_required + manager_required)."""
    from django.contrib.auth.decorators import login_required
    from ..permissions import manager_required
    staff_view = login_required(manager_required(view))

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        account = account_of(request) if request.user.is_authenticated else None
        if account is None:
            return staff_view(request, *args, **kwargs)
        if may_enter(account):
            return view(request, *args, **kwargs)
        raise PermissionDenied
    return wrapped


# ------------------------------------------------------------------ the shop's side
def make_link(customer, by):
    """A new one-time link for this customer: ``(token, account)``. Opens the account
    on the first one. Raises ValueError saying what is missing."""
    if customer.kind != Customer.REVENDA:
        raise ValueError('Only a Revenda customer can use the catalogue.')
    phone = phone_key(customer.phone)
    if len(phone) < 9:
        raise ValueError("Add the customer's phone first: it is the login.")
    with transaction.atomic():
        account = CustomerAccount.objects.select_for_update().filter(customer=customer).first()
        clash = CustomerAccount.objects.filter(phone=phone).exclude(pk=getattr(account, 'pk', None)).first()
        if clash is not None:
            raise ValueError(f'That phone already opens the account of {clash.customer.name}.')
        if account is None:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.create_user(f'customer-{customer.pk}-{secrets.token_hex(3)}')
            user.set_unusable_password()
            user.save()
            account = CustomerAccount.objects.create(user=user, customer=customer, phone=phone, invited_by=by)
        elif account.phone != phone:
            account.phone = phone
            account.save(update_fields=['phone'])
        account.links.filter(used_at__isnull=True).delete()
        token = secrets.token_urlsafe(32)
        AccountLink.objects.create(account=account, token_hash=digest(token), created_by=by,
                                   purpose=AccountLink.INVITE if account.status == CustomerAccount.INVITED
                                   else AccountLink.RESET,
                                   expires_at=timezone.now() + timedelta(hours=LINK_HOURS))
    return token, account


def set_status(account, status):
    account.status = status
    if status == CustomerAccount.SUSPENDED:
        account.suspended_at = timezone.now()
    account.save(update_fields=['status', 'suspended_at'])


# ------------------------------------------------------------------ the customer's side
def open_link(token):
    link = (AccountLink.objects.select_related('account__customer', 'account__user')
            .filter(token_hash=digest(token or ''), used_at__isnull=True, expires_at__gt=timezone.now()).first())
    if link is None or link.account.status == CustomerAccount.SUSPENDED:
        return None
    return link


def use_link(token, phone, password, again):
    """Set the password from a link. Returns the account; ValueError in Portuguese."""
    link = open_link(token)
    if link is None:
        raise ValueError(BAD_LINK)
    account = link.account
    if phone_key(phone) != account.phone:
        raise ValueError('Escreva o número de telemóvel registado na loja.')
    if password != again:
        raise ValueError('As palavras-passe não coincidem.')
    try:
        validate_password(password, account.user)
    except ValidationError:
        raise ValueError('Escolha uma palavra-passe com pelo menos 8 caracteres, que não seja só números nem '
                         'demasiado comum.')
    with transaction.atomic():
        if AccountLink.objects.filter(pk=link.pk, used_at__isnull=True).update(used_at=timezone.now()) != 1:
            raise ValueError(BAD_LINK)
        account.user.set_password(password)
        account.user.save(update_fields=['password'])
        if account.status == CustomerAccount.INVITED:
            account.status = CustomerAccount.ACTIVE
            account.activated_at = timezone.now()
            account.save(update_fields=['status', 'activated_at'])
    return account


def _miss_keys(phone, ip):
    return [f'portal-miss:p:{phone or "?"}', f'portal-miss:ip:{ip or "?"}']


def check_login(request, phone, password):
    """The customer's User, or ValueError. Five misses by phone or by address lock
    logging in for 15 minutes; every miss goes to the failed-login log."""
    key = phone_key(phone)
    ip = request.META.get('REMOTE_ADDR', '')
    keys = _miss_keys(key, ip)
    if any((cache.get(k) or 0) >= MAX_MISSES for k in keys):
        raise ValueError(LOCKED)
    account = (CustomerAccount.objects.select_related('user', 'customer').filter(phone=key).first()
               if len(key) >= 9 else None)
    user = authenticate(request, username=account.user.username, password=password) if account else None
    if user is None or not may_enter(account):
        if account is None:
            user_login_failed.send(sender=__name__, credentials={'username': f'phone {phone[:20]}'}, request=request)
        for k in keys:
            cache.add(k, 0, LOCK_SECONDS)
            cache.incr(k)
        raise ValueError(WRONG)
    cache.delete_many(keys)
    return user


def shop_name():
    from ..models import PrintProfile
    from .wholesale_orders import shop_store
    store = shop_store()
    return PrintProfile.get_for_store(store).name if store else ''
