"""The Finance section: admins only. The figures live in services/finance."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import BusinessSettings, Expense, ExpenseCategory, ExpenseTemplate, InboundOrder, SupplierPayment
from .models.expenses import PAID_METHODS
from .permissions import admin_required
from .services import finance
from .services.dashboard import resolve_dashboard_month
from .services.store_stock import active_stores
from .stores import resolve_active_store


def _shop(request):
    """The shop in view; None under All stores."""
    store, is_all = resolve_active_store(request)
    return None if is_all else store


def _back(request, default):
    nxt = (request.POST.get('next') or '').strip()
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return redirect(nxt)
    return redirect(default)


def _money(raw):
    try:
        return Decimal((raw or '').strip().replace(',', '.').replace('€', ''))
    except InvalidOperation:
        return None


def _month(raw):
    try:
        return datetime.strptime((raw or '').strip(), '%Y-%m').date()
    except ValueError:
        return None


@login_required
@admin_required
def overview_view(request):
    shop = _shop(request)
    month = _months_bar(request, 'finance')
    start, end = month['month_start'], month['period_end']
    prior_start = finance.previous_month(start)
    profit = finance.month_profit(start, end, shop)
    prior = finance.month_profit(prior_start, start - timedelta(days=1), shop)
    shops = active_stores()
    columns = [] if shop is not None or len(shops) < 2 else [
        {'shop': s, 'profit': finance.month_profit(start, end, s)} for s in shops]
    return render(request, 'stock/finance.html', {
        'tab': 'overview',
        'month': month,
        'shop': shop,
        'profit': profit,
        'prior': prior,
        'cash': finance.month_cash(start, end, shop),
        'unpaid': finance.unpaid(shop),
        'columns': columns,
        'rows': finance.profit_rows(profit, columns),
        'due': finance.due_templates(start, shop),
        'methods': PAID_METHODS,
        'last_method': request.session.get('finance_method', 'bank'),
    })


def _months_bar(request, url_name, **keep):
    """The ‹ month › bar's links, as on the dashboard."""
    month = resolve_dashboard_month(request.GET.get('month', '').strip(), timezone.localdate())
    params = {k: v for k, v in keep.items() if v}

    def link(value):
        return f"{reverse(url_name)}?{urlencode({**params, 'month': value})}"

    month['prev_url'] = link(month['prev_month_value'])
    month['next_url'] = link(month['next_month_value']) if month['next_month_value'] else ''
    return month


@login_required
@admin_required
def expenses_view(request):
    shops = active_stores()
    by_id = {s.id: s for s in shops}
    if request.method == 'POST':
        category = ExpenseCategory.objects.filter(pk=request.POST.get('category') or 0).first()
        expense = Expense.objects.filter(pk=request.POST.get('id') or 0).first()
        paid = request.POST.get('paid') == '1'
        method = (request.POST.get('method') or '').strip()
        try:
            store = by_id.get(int(request.POST.get('store') or 0))
            finance.save_expense(
                store=store, category=category, amount=_money(request.POST.get('amount')),
                period=_month(request.POST.get('month')),
                paid_on=(parse_date(request.POST.get('paid_on') or '') or timezone.localdate()) if paid else None,
                method=method, payee=request.POST.get('payee'), note=request.POST.get('note'),
                user=request.user, expense=expense)
            if paid:
                request.session['finance_method'] = method
            messages.success(request, 'Expense saved.')
        except ValidationError as e:
            messages.error(request, '; '.join(e.messages))
        return _back(request, 'finance_expenses')

    shop = _shop(request)
    category_id = (request.GET.get('category') or '').strip()
    status = (request.GET.get('status') or '').strip()
    month = _months_bar(request, 'finance_expenses', category=category_id, status=status)
    rows = (Expense.objects.filter(period=month['month_start'])
            .select_related('store', 'category').order_by('-paid_on', '-id'))
    if shop is not None:
        rows = rows.filter(store=shop)
    if category_id.isdigit():
        rows = rows.filter(category_id=int(category_id))
    if status == 'unpaid':
        rows = rows.filter(paid_on__isnull=True)
    elif status == 'paid':
        rows = rows.filter(paid_on__isnull=False)
    expenses = list(rows)
    today = timezone.localdate()
    return render(request, 'stock/finance_expenses.html', {
        'tab': 'expenses',
        'month': month,
        'expenses': expenses,
        'total': sum((e.amount for e in expenses), finance.ZERO),
        'categories': ExpenseCategory.objects.all(),
        'active_categories': ExpenseCategory.objects.filter(is_active=True),
        'used_categories': set(Expense.objects.values_list('category_id', flat=True)),
        'category_id': category_id,
        'status': status,
        'shops': shops,
        'shop': shop,
        'methods': PAID_METHODS,
        'last_method': request.session.get('finance_method', 'bank'),
        'today': today,
        'this_month': today.strftime('%Y-%m'),
    })


@login_required
@admin_required
@require_POST
def expense_delete(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    finance.log(request.user, 'deleted', 'expense', expense.id,
                f'{expense.store.name} · {expense.category.name} · {expense.period:%Y-%m} · €{expense.amount}')
    expense.delete()
    messages.success(request, 'Expense deleted.')
    return _back(request, 'finance_expenses')


@login_required
@admin_required
@require_POST
def expense_paid(request, expense_id):
    expense = get_object_or_404(Expense, pk=expense_id)
    try:
        finance.save_expense(store=expense.store, category=expense.category, amount=expense.amount,
                             period=expense.period,
                             paid_on=parse_date(request.POST.get('paid_on') or '') or timezone.localdate(),
                             method=(request.POST.get('method') or '').strip(), payee=expense.payee,
                             note=expense.note, user=request.user, expense=expense)
        messages.success(request, 'Marked as paid.')
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    return _back(request, 'finance_expenses')


@login_required
@admin_required
@require_POST
def category_view(request):
    action = request.POST.get('action')
    category = ExpenseCategory.objects.filter(pk=request.POST.get('id') or 0).first()
    if action == 'add':
        name = (request.POST.get('name') or '').strip()[:60]
        if name:
            ExpenseCategory.objects.get_or_create(name=name, defaults={'sort_order': 100})
    elif category is not None and action == 'toggle':
        category.is_active = not category.is_active
        category.save(update_fields=['is_active'])
    elif category is not None and action == 'delete':
        if category.expenses.exists() or category.templates.exists():
            messages.error(request, f'{category.name} is in use: deactivate it instead.')
        else:
            category.delete()
    return redirect('finance_expenses')


@login_required
@admin_required
def templates_view(request):
    shops = active_stores()
    by_id = {s.id: s for s in shops}
    if request.method == 'POST':
        template = ExpenseTemplate.objects.filter(pk=request.POST.get('id') or 0).first()
        if request.POST.get('action') == 'toggle' and template is not None:
            template.is_active = not template.is_active
            template.save(update_fields=['is_active'])
            return redirect('finance_templates')
        category = ExpenseCategory.objects.filter(pk=request.POST.get('category') or 0).first()
        amount = _money(request.POST.get('amount'))
        store = by_id.get(int(request.POST.get('store') or 0))
        if store is None or category is None or amount is None or amount <= 0:
            messages.error(request, 'Choose the shop and category, and enter the amount.')
            return redirect('finance_templates')
        row = template or ExpenseTemplate()
        row.store, row.category, row.amount = store, category, amount.quantize(Decimal('0.01'))
        row.payee = (request.POST.get('payee') or '').strip()[:120]
        row.note = (request.POST.get('note') or '').strip()[:200]
        row.save()
        finance.log(request.user, 'changed' if template else 'added', 'template', row.id, str(row))
        messages.success(request, 'Monthly cost saved.')
        return redirect('finance_templates')

    shop = _shop(request)
    month = _months_bar(request, 'finance_templates')
    rows = ExpenseTemplate.objects.select_related('store', 'category')
    if shop is not None:
        rows = rows.filter(store=shop)
    return render(request, 'stock/finance_templates.html', {
        'tab': 'monthly',
        'month': month,
        'templates': rows,
        'due': finance.due_templates(month['month_start'], shop),
        'categories': ExpenseCategory.objects.filter(is_active=True),
        'shops': shops,
        'shop': shop,
        'methods': PAID_METHODS,
        'last_method': request.session.get('finance_method', 'bank'),
    })


@login_required
@admin_required
@require_POST
def template_confirm(request, template_id):
    template = get_object_or_404(ExpenseTemplate, pk=template_id)
    period = _month(request.POST.get('month'))
    paid = request.POST.get('paid') == '1'
    method = (request.POST.get('method') or '').strip()
    try:
        finance.confirm_template(template, period, amount=_money(request.POST.get('amount')) or template.amount,
                                 paid_on=timezone.localdate() if paid else None, method=method, user=request.user)
        if paid:
            request.session['finance_method'] = method
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    return _back(request, 'finance_templates')


@login_required
@admin_required
@require_POST
def template_skip(request, template_id):
    template = get_object_or_404(ExpenseTemplate, pk=template_id)
    period = _month(request.POST.get('month'))
    if period is not None:
        finance.skip_template(template, period, request.user)
    return _back(request, 'finance_templates')


@login_required
@admin_required
def payables_view(request):
    groups = finance.payables()
    return render(request, 'stock/finance_payables.html', {
        'tab': 'payables',
        'groups': groups,
        'owed': sum((g['owed'] for g in groups), finance.ZERO),
        'recent': finance.recent_payments(),
        'shops': active_stores(),
        'shop': _shop(request),
        'payer_id': BusinessSettings.get().purchasing_store_id,
        'methods': PAID_METHODS,
        'last_method': request.session.get('finance_method', 'bank'),
        'today': timezone.localdate(),
    })


@login_required
@admin_required
@require_POST
def payment_add(request, order_id):
    order = get_object_or_404(InboundOrder, pk=order_id)
    by_id = {s.id: s for s in active_stores()}
    method = (request.POST.get('method') or '').strip()
    try:
        finance.add_payment(order, store=by_id.get(int(request.POST.get('store') or 0)),
                            amount=_money(request.POST.get('amount')),
                            paid_on=parse_date(request.POST.get('paid_on') or ''), method=method,
                            note=request.POST.get('note'), user=request.user)
        request.session['finance_method'] = method
        messages.success(request, f'Payment saved on inbound #{order.id}.')
    except ValidationError as e:
        messages.error(request, '; '.join(e.messages))
    return redirect('finance_payables')


@login_required
@admin_required
@require_POST
def payment_delete(request, payment_id):
    payment = get_object_or_404(SupplierPayment, pk=payment_id)
    finance.log(request.user, 'deleted', 'supplier payment', payment.id,
                f'Inbound #{payment.inbound_order_id} · {payment.store.name} · €{payment.amount}')
    payment.delete()
    messages.success(request, 'Payment deleted.')
    return redirect('finance_payables')
