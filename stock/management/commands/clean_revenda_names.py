"""Take the word "Revenda" out of customer names now that it is a field.

Before `Customer.kind` existed, the only way to mark a reseller was to type
"Revenda" into the name, so names like "Kapaika Revenda" carry the same fact
twice. Migration 0057 read it into the field; this takes it back out of the
name, and reports whether the field actually agrees.

Two refusals, because a wrong customer name shows up on paperwork:

* If the word is the whole name ("Revenda" - the walk-in reseller placeholder),
  stripping it would leave the row nameless. Left alone, and listed.
* If what is left is shorter than two characters, same thing.

Dry-run by default. ``--set-kind`` also marks anybody the name says is a
reseller but the field does not.
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import Customer

# Every form the shop typed, longest first so "revendedora" is not left as an
# "a" after "revendedor" matches.
WORDS = ('revendedoras', 'revendedores', 'revendedora', 'revendedor',
         'revendas', 'revenda')
WORD_RE = re.compile(r'\b(?:' + '|'.join(WORDS) + r')\b', re.IGNORECASE)
# What tends to be left behind once the word goes: a dangling dash, slash,
# comma, or an empty bracket.
LEFTOVERS = re.compile(r'(\(\s*\)|\[\s*\]|[\-/,;:]\s*$|^\s*[\-/,;:])')

MIN_NAME = 2


def strip_revenda(name):
    """The name without the word, or '' when there would be nothing left."""
    out = WORD_RE.sub(' ', name or '')
    for _ in range(3):
        out = LEFTOVERS.sub(' ', out).strip()
    out = re.sub(r'\s{2,}', ' ', out).strip(' -/,;:')
    return out if len(out) >= MIN_NAME else ''


class Command(BaseCommand):
    help = 'Remove the word "Revenda" from customer names (kept in Customer.kind).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')
        parser.add_argument('--set-kind', action='store_true',
                            help='also mark as Revenda anybody the name says is one')

    def handle(self, *args, **opts):
        matches = list(Customer.objects.filter(name__icontains='revend')
                       .order_by('name'))
        if not matches:
            self.stdout.write('No customer name contains "revend" - nothing to do.')
            return

        renames, untouched, needs_kind = [], [], []
        for customer in matches:
            if customer.kind != Customer.REVENDA:
                needs_kind.append(customer)
            new_name = strip_revenda(customer.name)
            if new_name and new_name != customer.name:
                renames.append((customer, new_name))
            else:
                untouched.append(customer)

        self.stdout.write(f'  names containing "revend"   {len(matches)}')
        self.stdout.write(f'  would be renamed            {len(renames)}')
        self.stdout.write(f'  left alone                  {len(untouched)}')
        self.stdout.write(f'  not marked Revenda yet      {len(needs_kind)}')
        self.stdout.write('')

        for customer, new_name in renames:
            flag = '' if customer.kind == Customer.REVENDA else '  [kind is Retail]'
            self.stdout.write(f'    {customer.nif}  "{customer.name}" -> "{new_name}"{flag}')
        for customer in untouched:
            flag = '' if customer.kind == Customer.REVENDA else '  [kind is Retail]'
            self.stdout.write(self.style.WARNING(
                f'    {customer.nif}  "{customer.name}" - the word is the whole '
                f'name, left as it is{flag}'))

        if needs_kind and not opts['set_kind']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'Some of the above are still Retail. Add --set-kind to mark them.'))

        if not opts['apply']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING('Dry run - nothing written. Use --apply.'))
            return

        with transaction.atomic():
            for customer, new_name in renames:
                customer.name = new_name
                customer.save(update_fields=['name'])
            if opts['set_kind']:
                for customer in needs_kind:
                    customer.kind = Customer.REVENDA
                    customer.save(update_fields=['kind'])

        self.stdout.write(self.style.SUCCESS(
            f'Renamed {len(renames)}'
            + (f', marked {len(needs_kind)} as Revenda' if opts['set_kind'] else '')))
