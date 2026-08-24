"""Give the accessory categories a sensible starting set of attributes.

Everything here is editable in the admin afterwards - this only saves the shop
from typing the obvious ones by hand. Re-running it adds what is missing and
leaves anything already defined alone, so it is safe after hand-editing.

Dry-run by default; pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from stock.models import AttributeOption, Category, CategoryAttribute

# category name -> attributes. Each attribute is
# (name, code, data_type, variant?, unit, [options])
#
# "variant?" is the question "do two of these need separate stock rows?".
# A full-glue curved protector and a normal flat one have different costs and
# different prices, so they are separate products - that is what variant means
# here. A hardness rating is just a description, so it is not.
PLAN = {
    'Accessories': [
        ('Colour', 'colour', 'choice', True, '',
         ['Black', 'Clear / Transparent', 'White', 'Blue', 'Navy', 'Red',
          'Green', 'Pink', 'Purple', 'Gold', 'Silver', 'Grey', 'Beige',
          'Multicolour']),
    ],
    'Cases': [
        ('Case type', 'case_type', 'choice', True, '',
         ['Plain silicone / rubber', 'Clear TPU', 'Fancy / printed',
          'MagSafe', 'Flip / wallet', 'Rugged / shockproof',
          'Leather', 'Camera-protect', 'Bumper frame']),
        ('Wireless charging', 'wireless_ok', 'boolean', False, '', []),
    ],
    'Screen protectors': [
        ('Protector type', 'protector_type', 'choice', True, '',
         ['Tempered glass', 'Hydrogel film', 'Privacy', 'Matte / anti-glare',
          'Camera lens glass']),
        ('Glue', 'glue', 'choice', True, '',
         ['Full glue', 'Edge glue', 'UV glue']),
        ('Edge', 'edge', 'choice', True, '',
         ['Flat 2.5D', 'Curved 3D', 'Full cover']),
        ('Hardness', 'hardness', 'text', False, '', []),
    ],
    'Cables': [
        ('Connector', 'connector', 'choice', True, '',
         ['USB-C to USB-C', 'USB-C to Lightning', 'USB-A to USB-C',
          'USB-A to Lightning', 'USB-A to Micro-USB', 'USB-C to 3.5mm',
          'HDMI', 'Other']),
        ('Length', 'length_m', 'number', True, 'm', []),
        ('Fast charge', 'fast_charge', 'boolean', False, '', []),
    ],
    'Chargers & plugs': [
        ('Charger type', 'charger_type', 'choice', True, '',
         ['Wall plug', 'Car charger', 'Wireless pad', 'MagSafe puck',
          'Multi-port hub', 'Travel adapter']),
        ('Power', 'power_w', 'number', True, 'W', []),
        ('Ports', 'ports', 'text', False, '', []),
    ],
    'Power banks': [
        ('Capacity', 'capacity_mah', 'number', True, 'mAh', []),
        ('Output', 'output_w', 'number', False, 'W', []),
        ('Built-in cable', 'built_in_cable', 'boolean', False, '', []),
    ],
    'Audio': [
        ('Audio type', 'audio_type', 'choice', True, '',
         ['Wired earphones', 'TWS earbuds', 'Neckband', 'Over-ear headphones',
          'Bluetooth speaker', 'Gaming headset']),
        ('Connection', 'audio_connection', 'choice', True, '',
         ['Bluetooth', 'Wired 3.5mm', 'Wired USB-C', 'Wired Lightning']),
        ('Noise cancelling', 'anc', 'boolean', False, '', []),
    ],
    'Mice & keyboards': [
        ('Device type', 'peripheral_type', 'choice', True, '',
         ['Mouse', 'Keyboard', 'Keyboard + mouse set', 'Mousepad',
          'Presenter / clicker']),
        ('Connection', 'peripheral_connection', 'choice', True, '',
         ['Wired USB', 'Wireless 2.4G', 'Bluetooth', 'Dual mode']),
        ('Layout', 'layout', 'choice', False, '',
         ['PT', 'ES', 'UK', 'US', 'Not applicable']),
        ('Rechargeable', 'rechargeable', 'boolean', False, '', []),
    ],
    'Storage': [
        ('Storage type', 'storage_type', 'choice', True, '',
         ['MicroSD card', 'SD card', 'USB flash drive', 'External SSD',
          'Card reader']),
        ('Capacity', 'capacity_gb', 'number', True, 'GB', []),
        ('Speed class', 'speed_class', 'text', False, '', []),
    ],
    'Holders & mounts': [
        ('Mount type', 'mount_type', 'choice', True, '',
         ['Car vent', 'Car dashboard', 'MagSafe car', 'Desk stand',
          'Ring holder / grip', 'Bike mount', 'Tripod / selfie stick']),
    ],
    'Other electronics': [
        ('Item type', 'item_type', 'text', True, '', []),
    ],
}

# Subcategories created under Accessories so the tree carries Colour down.
CHILDREN = ['Cases', 'Screen protectors', 'Cables', 'Chargers & plugs',
            'Power banks', 'Audio', 'Mice & keyboards', 'Storage',
            'Holders & mounts', 'Other electronics']


class Command(BaseCommand):
    help = 'Create the starting attribute set for accessory categories.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='write changes (default: dry run)')
        parser.add_argument('--prune', action='store_true',
                            help='also remove attributes no longer in the plan '
                                 '(only ones no product has answered)')

    def handle(self, *args, **opts):
        apply = opts['apply']
        created = {'categories': 0, 'attributes': 0, 'options': 0, 'pruned': 0}

        with transaction.atomic():
            parent = Category.objects.filter(name__iexact='Accessories').first()
            if parent is None:
                # form_kind drives which questions the add-product page asks;
                # without it a new shop would get the generic form for cases.
                parent = Category(name='Accessories', form_kind='accessory',
                                  sync_to_shopify=False)
                created['categories'] += 1
                if apply:
                    parent.save()

            for name in CHILDREN:
                if not Category.objects.filter(name__iexact=name).exists():
                    created['categories'] += 1
                    if apply:
                        Category.objects.create(name=name, parent=parent,
                                                form_kind='accessory',
                                                sync_to_shopify=False)

            for category_name, attributes in PLAN.items():
                category = Category.objects.filter(name__iexact=category_name).first()
                for order, spec in enumerate(attributes, start=1):
                    label, code, kind, variant, unit, options = spec
                    attribute = None
                    if category is not None:
                        attribute = CategoryAttribute.objects.filter(
                            category=category, code=code).first()
                    if attribute is None:
                        created['attributes'] += 1
                        if apply:
                            attribute = CategoryAttribute.objects.create(
                                category=category, name=label, code=code,
                                data_type=kind, unit=unit,
                                variant_attribute=variant, sort_order=order)
                    if attribute is None:
                        # Dry run on a category that does not exist yet: every
                        # option would be new, so say so rather than under-count.
                        created['options'] += len(options)
                        continue
                    for position, option_label in enumerate(options, start=1):
                        if attribute.options.filter(label__iexact=option_label).exists():
                            continue
                        created['options'] += 1
                        if apply:
                            AttributeOption.objects.create(
                                attribute=attribute, label=option_label,
                                sort_order=position)

            if opts['prune']:
                created['pruned'] = self._prune(apply)

            if not apply:
                transaction.set_rollback(True)

        for key, value in created.items():
            self.stdout.write(f'  {key:<14} {value}')
        self.stdout.write(self.style.SUCCESS('Applied.') if apply
                          else self.style.WARNING('Dry run - nothing written. Use --apply.'))

    def _prune(self, apply):
        """Drop attributes this plan no longer defines.

        Renaming a code leaves the old attribute behind, so the form ends up
        asking the same thing twice. Only attributes **nobody has answered**
        are removed - a real answer is shop data and is never thrown away.
        """
        pruned = 0
        for category_name, attributes in PLAN.items():
            category = Category.objects.filter(name__iexact=category_name).first()
            if category is None:
                continue
            wanted = {spec[1] for spec in attributes}
            stale = (CategoryAttribute.objects
                     .filter(category=category)
                     .exclude(code__in=wanted)
                     .filter(values__isnull=True))
            pruned += stale.count()
            if apply:
                stale.delete()
        return pruned
