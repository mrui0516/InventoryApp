"""Shelf map: is there stock, and is there more above the shelf?

Phone accessories are not counted. A shop assistant needs to answer "do we
have a black one for a 15 Pro?" in a second, and "do I need to fetch more from
the box above the shelf?" - not how many are left. Counting them would be work
nobody does, and a count nobody maintains is worse than no count, because
people believe it.

So one row per **style x model x option** carries a single state:

* ``OUT``     - none left, ask the customer to come back
* ``DISPLAY`` - on the shelf, and that is all there is
* ``EXTRA``   - on the shelf, with more in the box above it

The grid is deliberately built from two axes that are *data*:

* ``ShelfStyle`` is what is being stocked - plain silicone today, MagSafe or
  tempered glass tomorrow. Adding one is a row, not a release.
* ``ShelfAxis`` + ``ShelfOption`` are the columns. Cases are stocked by
  colour; screen protectors are not - they are stocked by glue and edge. So
  each style names the axis that forms its columns, and a new kind of goods
  brings its own columns without touching this file.

**Phone models are shared.** ``DeviceModel`` is one registry for the whole
app, so a handset entered once shows up under every style that follows - which
is the point: enter iPhone 17 Pro once, and it is there for silicone cases,
MagSafe cases and glass alike.

Everything here is arranged so that real quantities, purchases and sales can
be added later without any of it being rebuilt: the state is a field on a row
that already has the right identity, not a pair of booleans.
"""
import re

from django.conf import settings
from django.db import models

OUT = 'out'
DISPLAY = 'display'
EXTRA = 'extra'

STOCK_STATES = [
    (OUT, 'Out of stock'),
    (DISPLAY, 'On shelf'),
    (EXTRA, 'On shelf + extra above'),
]

# Tapping a cell walks this cycle, which is the order things happen in: a
# colour runs out, gets restocked from the box above, then the box is emptied.
NEXT_STATE = {OUT: DISPLAY, DISPLAY: EXTRA, EXTRA: OUT}


def natural_key(text):
    """Sort key that reads numbers as numbers.

    Plain alphabetical ordering puts "iPhone 11" before "iPhone 9", which is
    not how anybody looks down a shelf. Digits are zero-padded so the database
    can do the ordering itself and a newly added model lands in its place with
    no re-sorting pass.
    """
    parts = re.split(r'(\d+)', (text or '').lower())
    return ''.join(part.rjust(8, '0') if part.isdigit() else part
                   for part in parts).strip()


class ShelfAxis(models.Model):
    """What the columns of a grid mean - "Colour", "Glue & edge", "Capacity".

    Held separately so a new kind of goods brings its own columns. Cases are
    stocked by colour and glass is not, and neither needs the other's columns
    cluttering its grid.
    """
    name = models.CharField(max_length=40, unique=True)
    slug = models.SlugField(max_length=40, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'name']
        verbose_name_plural = 'shelf axes'

    def __str__(self):
        return self.name


class ShelfOption(models.Model):
    """One column: a colour, a glue type, whatever the axis calls for."""
    axis = models.ForeignKey(ShelfAxis, on_delete=models.CASCADE,
                             related_name='options')
    name = models.CharField(max_length=40)
    # Optional swatch so a colour grid shows the colour, not just its name.
    swatch = models.CharField(max_length=7, blank=True, help_text='e.g. #1f2937')
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = [('axis', 'name')]

    def __str__(self):
        return self.name


class ShelfStyle(models.Model):
    """A thing the shop stocks per handset - a case material, a glass type.

    Plain silicone to begin with. Each style names the axis that forms its
    columns, so adding "Tempered glass" with a glue-and-edge axis needs no
    code.
    """
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    axis = models.ForeignKey(ShelfAxis, on_delete=models.PROTECT,
                             related_name='styles')
    # Which catalogue category this style stands in for. Cases and glass are
    # never entered as products - you add the handset once and then say which
    # colours are on the shelf - so the add-product page sends anyone choosing
    # that category here instead of showing them a form they should not fill.
    category = models.ForeignKey('stock.Category', null=True, blank=True,
                                 on_delete=models.SET_NULL,
                                 related_name='shelf_styles')
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def columns(self):
        return self.axis.options.filter(is_active=True)


class ShelfStock(models.Model):
    """The state of one style x model x option on the shelf."""
    style = models.ForeignKey(ShelfStyle, on_delete=models.CASCADE,
                              related_name='stock_rows')
    model = models.ForeignKey('stock.DeviceModel', on_delete=models.CASCADE,
                              related_name='shelf_stock')
    option = models.ForeignKey(ShelfOption, on_delete=models.CASCADE,
                               related_name='shelf_stock')
    state = models.CharField(max_length=8, choices=STOCK_STATES, default=OUT,
                             db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta:
        unique_together = [('style', 'model', 'option')]
        ordering = ['model', 'option']

    def __str__(self):
        return f'{self.style} / {self.model} / {self.option}: {self.state}'

    @property
    def in_stock(self):
        return self.state != OUT

    def cycle(self, user=None):
        """Advance to the next state - what a tap on the grid does."""
        self.state = NEXT_STATE[self.state]
        self.updated_by = user if (user and user.is_authenticated) else None
        self.save(update_fields=['state', 'updated_at', 'updated_by'])
        return self.state


class ModelShelfNote(models.Model):
    """A free note against one phone model in one style's grid.

    Used today to record that an accessory fits several handsets ("same as
    14 Pro"). It is a reminder for the person at the shelf and nothing more -
    the states of two models are not linked, so each is marked on its own.
    Real compatibility already exists as CompatibilityGroup and can drive this
    later without the notes being thrown away.
    """
    style = models.ForeignKey(ShelfStyle, on_delete=models.CASCADE,
                              related_name='model_notes')
    model = models.ForeignKey('stock.DeviceModel', on_delete=models.CASCADE,
                              related_name='shelf_notes')
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        unique_together = [('style', 'model')]

    def __str__(self):
        return f'{self.model}: {self.note}'
