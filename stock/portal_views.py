"""The customer's way in: the link to set a password, logging in and out, and the
shop's controls on the customer page. Customer pages are Portuguese."""
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Customer, CustomerAccount
from .permissions import manager_required
from .services import portal

BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _enter(request, user):
    login(request, user, backend=BACKEND)
    request.session.set_expiry(portal.SESSION_SECONDS)


def _next(request):
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return reverse('catalog')


def portal_home(request):
    return redirect('catalog')


def portal_login(request):
    error = ''
    if request.method == 'POST':
        try:
            user = portal.check_login(request, request.POST.get('phone') or '', request.POST.get('password') or '')
        except ValueError as e:
            error = str(e)
        else:
            _enter(request, user)
            return redirect(_next(request))
    return render(request, 'stock/portal_login.html', {'error': error, 'portal_shop': portal.shop_name(),
                                                       'next': request.GET.get('next', ''),
                                                       'phone': request.POST.get('phone', '')})


@require_POST
def portal_logout(request):
    logout(request)
    return redirect('portal_login')


def portal_invite(request, token):
    link = portal.open_link(token)
    error = ''
    if request.method == 'POST':
        try:
            account = portal.use_link(token, request.POST.get('phone') or '', request.POST.get('password1') or '',
                                      request.POST.get('password2') or '')
        except ValueError as e:
            error = str(e)
            link = portal.open_link(token)
        else:
            _enter(request, account.user)
            return redirect('catalog')
    return render(request, 'stock/portal_invite.html', {'link': link, 'error': error, 'portal_shop': portal.shop_name(),
                                                        'bad_link': portal.BAD_LINK})


@login_required
@manager_required
def b2b_overview(request):
    """B2B -> Overview: every reseller, its account and what it does."""
    from .services import b2b_overview as overview
    state = request.GET.get('state', '')
    state = state if state in overview.STATES else ''
    query = request.GET.get('q', '').strip()
    data = overview.overview(state, query)
    return render(request, 'stock/b2b_overview.html', {
        'data': data, 'rows': data['rows'], 'state': state, 'query': query,
        'state_tabs': [(s, overview.STATE_LABELS[s], data['counts'][s]) for s in overview.STATES],
    })


@login_required
@manager_required
@require_POST
def customer_portal(request, customer_id):
    """Customer page -> Catalogue access: a link, suspending, resuming."""
    customer = get_object_or_404(Customer, pk=customer_id)
    action = request.POST.get('action')
    account = CustomerAccount.objects.filter(customer=customer).first()
    try:
        if action == 'invite':
            token, account = portal.make_link(customer, request.user)
            request.session['portal_link'] = request.build_absolute_uri(reverse('portal_invite', args=[token]))
            request.session['portal_link_for'] = customer.pk
        elif action in ('suspend', 'resume') and account is not None:
            portal.set_status(account, CustomerAccount.SUSPENDED if action == 'suspend' else CustomerAccount.ACTIVE)
            messages.success(request, 'Access suspended.' if action == 'suspend' else 'Access open again.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('customer_detail', customer_id=customer.pk)
