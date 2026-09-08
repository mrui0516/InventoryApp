"""Move the note layers out of the descriptions and into their own fields.

The notes were typed into the description long before they had fields, so
several hundred products carry them as prose. Copying those across by hand is
an afternoon of work nobody should do twice.

Anything the description does not clearly state is left alone and listed at the
end, so it is obvious which products still want a human. A wrong note on a
listing is worse than a missing one, so the parser refuses anything that reads
like a sentence rather than a list of ingredients.

Dry-run by default; pass --apply to write.

    python manage.py backfill_perfume_notes             # see what it would do
    python manage.py backfill_perfume_notes --apply     # write it
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import Product
from stock.services.note_parser import parse_notes

FIELD_FOR = {'top': 'notes_top', 'heart': 'notes_heart', 'base': 'notes_base'}


class Command(BaseCommand):
    help = 'Fill the perfume note fields from what is already in each description.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')
        parser.add_argument('--overwrite', action='store_true',
                            help='replace notes already entered by hand '
                                 '(default: leave them alone)')
        parser.add_argument('--show', type=int, default=25,
                            help='how many products to list in each section')

    def handle(self, *args, **opts):
        apply = opts['apply']
        overwrite = opts['overwrite']
        show = opts['show']

        perfumes = (Product.objects
                    .filter(category__name__icontains='perfum')
                    .order_by('brand', 'name'))

        filled, partial, skipped, kept = [], [], [], 0

        with transaction.atomic():
            for product in perfumes:
                found = parse_notes(product.description)
                changes = {}
                for layer, field in FIELD_FOR.items():
                    value = found[layer]
                    if not value:
                        continue
                    if getattr(product, field) and not overwrite:
                        kept += 1        # entered by hand already; left as is
                        continue
                    changes[field] = value

                if not changes:
                    skipped.append(product)
                    continue

                if apply:
                    for field, value in changes.items():
                        setattr(product, field, value)
                    product.save(update_fields=sorted(changes))

                (filled if len(changes) == 3 else partial).append((product, changes))

            if not apply:
                transaction.set_rollback(True)

        self._report(filled, partial, skipped, kept, show, apply)

    def _report(self, filled, partial, skipped, kept, show, apply):
        out = self.stdout
        out.write('')
        out.write(f'  all three layers   {len(filled)}')
        out.write(f'  some layers        {len(partial)}')
        out.write(f'  nothing found      {len(skipped)}')
        if kept:
            out.write(f'  left alone         {kept} field(s) already filled in')

        if partial:
            out.write('')
            out.write(self.style.WARNING('Partly read - worth a look:'))
            for product, changes in partial[:show]:
                got = ', '.join(sorted(changes))
                out.write(f'   {product.brand} {product.name}  ->  {got}')
            if len(partial) > show:
                out.write(f'   ... and {len(partial) - show} more')

        if skipped:
            out.write('')
            out.write(self.style.WARNING(
                'No notes found in the description - these still need entering '
                'by hand:'))
            for product in skipped[:show]:
                out.write(f'   {product.brand} {product.name}')
            if len(skipped) > show:
                out.write(f'   ... and {len(skipped) - show} more')

        out.write('')
        out.write(self.style.SUCCESS('Applied.') if apply
                  else self.style.WARNING('Dry run - nothing written. Use --apply.'))
