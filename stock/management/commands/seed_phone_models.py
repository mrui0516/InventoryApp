"""Fill in the handset registry so the shelf grid starts with real rows.

Models are shared by every style: entered once here, an iPhone shows up under
silicone cases, MagSafe cases and tempered glass alike. Adding one by hand on
the shelf page does the same thing - this is only bulk entry.

Release years are recorded because they are useful later (and make the
ordering obvious to read), but the grid sorts on the model name, so a handset
added without a year still lands in the right place.

Dry-run by default; pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import Brand, DeviceModel

# (name, release year, [aliases]) - aliases are how customers and staff say it
IPHONES = [
    ('iPhone 6', 2014, []),
    ('iPhone 6 Plus', 2014, ['6+']),
    ('iPhone 6s', 2015, []),
    ('iPhone 6s Plus', 2015, ['6s+']),
    ('iPhone SE (2016)', 2016, ['SE1', 'SE 1']),
    ('iPhone 7', 2016, []),
    ('iPhone 7 Plus', 2016, ['7+']),
    ('iPhone 8', 2017, []),
    ('iPhone 8 Plus', 2017, ['8+']),
    ('iPhone X', 2017, ['10']),
    ('iPhone XR', 2018, ['10R']),
    ('iPhone XS', 2018, ['10S']),
    ('iPhone XS Max', 2018, ['10S Max', 'XSM']),
    ('iPhone 11', 2019, []),
    ('iPhone 11 Pro', 2019, ['11P']),
    ('iPhone 11 Pro Max', 2019, ['11PM']),
    ('iPhone SE (2020)', 2020, ['SE2', 'SE 2']),
    ('iPhone 12 mini', 2020, ['12m']),
    ('iPhone 12', 2020, []),
    ('iPhone 12 Pro', 2020, ['12P']),
    ('iPhone 12 Pro Max', 2020, ['12PM']),
    ('iPhone 13 mini', 2021, ['13m']),
    ('iPhone 13', 2021, []),
    ('iPhone 13 Pro', 2021, ['13P']),
    ('iPhone 13 Pro Max', 2021, ['13PM']),
    ('iPhone SE (2022)', 2022, ['SE3', 'SE 3']),
    ('iPhone 14', 2022, []),
    ('iPhone 14 Plus', 2022, ['14+']),
    ('iPhone 14 Pro', 2022, ['14P']),
    ('iPhone 14 Pro Max', 2022, ['14PM']),
    ('iPhone 15', 2023, []),
    ('iPhone 15 Plus', 2023, ['15+']),
    ('iPhone 15 Pro', 2023, ['15P']),
    ('iPhone 15 Pro Max', 2023, ['15PM']),
    ('iPhone 16', 2024, []),
    ('iPhone 16 Plus', 2024, ['16+']),
    ('iPhone 16 Pro', 2024, ['16P']),
    ('iPhone 16 Pro Max', 2024, ['16PM']),
    ('iPhone 16e', 2025, []),
]

LINEUPS = {'Apple': IPHONES}


class Command(BaseCommand):
    help = 'Bulk-add handsets to the shared model registry.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')
        parser.add_argument('--brand', default='Apple',
                            help='which lineup to seed (default: Apple)')

    def handle(self, *args, **opts):
        apply = opts['apply']
        wanted = opts['brand']
        lineup = LINEUPS.get(wanted)
        if lineup is None:
            self.stdout.write(self.style.ERROR(
                f'No lineup for {wanted}. Known: {", ".join(LINEUPS)}'))
            return

        made = {'models': 0, 'aliases': 0, 'already there': 0}

        with transaction.atomic():
            brand = Brand.objects.filter(name__iexact=wanted).first()
            if brand is None:
                brand = Brand(name=wanted)
                if apply:
                    brand.save()

            for name, year, aliases in lineup:
                model = (DeviceModel.objects.filter(brand=brand, name__iexact=name).first()
                         if brand.pk else None)
                if model is not None:
                    made['already there'] += 1
                else:
                    made['models'] += 1
                    if apply:
                        model = DeviceModel.objects.create(
                            brand=brand, name=name, release_year=year)

                for alias in aliases:
                    if model is None:
                        made['aliases'] += 1
                        continue
                    # An alias is unique across the table, so one already
                    # pointing elsewhere is left alone rather than stolen.
                    from stock.models import DeviceAlias
                    from stock.models.devices import normalise_device_text
                    key = normalise_device_text(alias)
                    if DeviceAlias.objects.filter(normalised=key).exists():
                        continue
                    made['aliases'] += 1
                    if apply:
                        DeviceAlias.objects.create(device=model, alias=alias)

            if not apply:
                transaction.set_rollback(True)

        for key, value in made.items():
            self.stdout.write(f'  {key:<14} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))
