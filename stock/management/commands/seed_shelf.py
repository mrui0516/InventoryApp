"""Start the shelf map: the silicone-case style and the usual colours.

Everything is editable in the app afterwards. Re-running adds only what is
missing, so it is safe after the shop has made its own changes.

Dry-run by default; pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from stock.models import CaseStyle, ShelfColour

STYLES = [('Normal silicone', 1)]

COLOURS = [
    ('Black', '#111827', 1),
    ('Transparent', '#e5e7eb', 2),
    ('Blue', '#2563eb', 3),
    ('Navy', '#1e3a8a', 4),
    ('Pink', '#ec4899', 5),
    ('Red', '#dc2626', 6),
    ('Green', '#16a34a', 7),
    ('Purple', '#7c3aed', 8),
    ('White', '#f9fafb', 9),
    ('Grey', '#6b7280', 10),
]


class Command(BaseCommand):
    help = 'Create the starting case style and colours for the shelf map.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')

    def handle(self, *args, **opts):
        apply = opts['apply']
        made = {'styles': 0, 'colours': 0}

        with transaction.atomic():
            for name, order in STYLES:
                if not CaseStyle.objects.filter(slug=slugify(name)).exists():
                    made['styles'] += 1
                    if apply:
                        CaseStyle.objects.create(name=name, slug=slugify(name),
                                                 sort_order=order)
            for name, swatch, order in COLOURS:
                if not ShelfColour.objects.filter(name__iexact=name).exists():
                    made['colours'] += 1
                    if apply:
                        ShelfColour.objects.create(name=name, swatch=swatch,
                                                   sort_order=order)
            if not apply:
                transaction.set_rollback(True)

        for key, value in made.items():
            self.stdout.write(f'  {key:<10} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))
