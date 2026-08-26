"""Start the shelf map: the silicone-case style and the usual colours.

Everything is editable in the app afterwards. Re-running adds only what is
missing, so it is safe after the shop has made its own changes.

Dry-run by default; pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from stock.models import Category, ShelfAxis, ShelfOption, ShelfStyle

# style name -> which axis forms its columns
# style name -> (axis slug, catalogue category it stands in for, order)
STYLES = [('Normal silicone', 'colour', 'Cases', 1)]

AXES = [('Colour', 'colour', 1), ('Glue & edge', 'glue-edge', 2)]

# axis slug -> options
OPTIONS = {'glue-edge': [
    ('Full glue flat', '', 1),
    ('Full glue curved', '', 2),
    ('Edge glue flat', '', 3),
    ('Privacy', '', 4),
]}

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
        made = {'axes': 0, 'styles': 0, 'options': 0}

        with transaction.atomic():
            axes = {}
            for name, slug, order in AXES:
                axis = ShelfAxis.objects.filter(slug=slug).first()
                if axis is None:
                    made['axes'] += 1
                    if apply:
                        axis = ShelfAxis.objects.create(name=name, slug=slug,
                                                        sort_order=order)
                axes[slug] = axis

            for name, axis_slug, category_name, order in STYLES:
                if not ShelfStyle.objects.filter(slug=slugify(name)).exists():
                    made['styles'] += 1
                    if apply:
                        ShelfStyle.objects.create(
                            name=name, slug=slugify(name), axis=axes[axis_slug],
                            # Links the grid to the catalogue category, so
                            # add-product sends people here instead of showing
                            # them a form for goods that are never products.
                            category=Category.objects.filter(
                                name__iexact=category_name).first(),
                            sort_order=order)

            colour_axis = axes.get('colour')
            for name, swatch, order in COLOURS:
                if colour_axis is None:
                    made['options'] += 1
                    continue
                if not ShelfOption.objects.filter(axis=colour_axis,
                                                  name__iexact=name).exists():
                    made['options'] += 1
                    if apply:
                        ShelfOption.objects.create(axis=colour_axis, name=name,
                                                   swatch=swatch, sort_order=order)

            for axis_slug, options in OPTIONS.items():
                axis = axes.get(axis_slug)
                for name, swatch, order in options:
                    if axis is None:
                        made['options'] += 1
                        continue
                    if not ShelfOption.objects.filter(axis=axis,
                                                      name__iexact=name).exists():
                        made['options'] += 1
                        if apply:
                            ShelfOption.objects.create(axis=axis, name=name,
                                                       swatch=swatch,
                                                       sort_order=order)

            if not apply:
                transaction.set_rollback(True)

        for key, value in made.items():
            self.stdout.write(f'  {key:<10} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))
