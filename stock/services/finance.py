"""The Finance section's figures: what was spent, owed, paid and earned."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from ..models import Expense, ExpenseTemplate, ExpenseTemplateSkip, FinanceLog
from ..models.expenses import PAID_METHODS

ZERO = Decimal('0.00')


def log(user, action, kind, ref_id, summary):
    FinanceLog.objects.create(user=user, action=action, kind=kind, ref_id=ref_id, summary=summary[:255])


def save_expense(*, store, category, amount, period, paid_on, method, payee, note, user, expense=None, template=None):
    """Add ``expense``, or change the one given. ``period`` lands on the 1st of
    its month; an unpaid expense has no method."""
    if store is None or category is None:
        raise ValidationError('Choose the shop and the category.')
    if amount is None or amount <= 0:
        raise ValidationError('Enter the amount.')
    if period is None:
        raise ValidationError('Choose the month.')
    if paid_on and method not in dict(PAID_METHODS):
        raise ValidationError('Choose how it was paid.')
    row = expense or Expense(created_by=user, template=template)
    before = None if expense is None else f'€{expense.amount}'
    row.store, row.category, row.amount = store, category, amount.quantize(Decimal('0.01'))
    row.period = period.replace(day=1)
    row.paid_on, row.method = paid_on, (method if paid_on else '')
    row.payee, row.note = (payee or '')[:120], (note or '')[:200]
    row.save()
    change = f'{before} -> ' if before else ''
    log(user, 'changed' if expense else 'added', 'expense', row.id,
        f'{row.store.name} · {row.category.name} · {row.period:%Y-%m} · {change}€{row.amount}')
    return row


# ------------------------------------------------------------ monthly costs
def due_templates(period, store=None):
    """The monthly costs of ``period`` not confirmed or skipped yet. Worked out
    when asked, so nothing has to run at the start of a month."""
    templates = ExpenseTemplate.objects.filter(is_active=True).select_related('store', 'category')
    if store is not None:
        templates = templates.filter(store=store)
    done = set(Expense.objects.filter(period=period, template__isnull=False).values_list('template_id', flat=True))
    done |= set(ExpenseTemplateSkip.objects.filter(period=period).values_list('template_id', flat=True))
    return [t for t in templates if t.id not in done]


def confirm_template(template, period, *, amount, paid_on, method, user):
    """The month's expense from ``template``; a second confirm finds the first."""
    found = Expense.objects.filter(template=template, period=period).first()
    if found is not None:
        return found
    try:
        with transaction.atomic():
            return save_expense(store=template.store, category=template.category, amount=amount, period=period,
                                paid_on=paid_on, method=method, payee=template.payee, note=template.note,
                                user=user, template=template)
    except IntegrityError:          # two taps at once: the other one saved it
        return Expense.objects.get(template=template, period=period)


def skip_template(template, period, user):
    ExpenseTemplateSkip.objects.get_or_create(template=template, period=period)
    log(user, 'skipped', 'template', template.id, f'{template} · {period:%Y-%m}')
