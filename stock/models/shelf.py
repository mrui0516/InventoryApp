"""Shelf map: is there stock, and is there more above the shelf?

Phone cases are not counted. A shop assistant needs to answer "do we have a
black one for a 15 Pro?" in a second, and "do I need to fetch more from the
box above the shelf?" - not how many are left. Counting them would be work
nobody does, and a count nobody maintains is worse than no count.

So one row per **style x model x colour** carries a single state:

* ``OUT``     - none left, ask the customer to come back
* ``DISPLAY`` - on the shelf, and that is all there is
* ``EXTRA``   - on the shelf, with more in the box above it

When a colour sells out the assistant marks it OUT. When the box above is
emptied onto the shelf, EXTRA becomes DISPLAY. Neither needs a number.

The pieces are kept separate - style, model, colour, state - rather than
stored as text like "IP15 Pro: B(up), T", so the same data can later carry
real quantities, purchases and sales without any of this being rebuilt. The
state field is deliberately not a boolean pair for that reason: adding a
``quantity`` column later leaves every row here still meaningful.
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

# Clicking a cell walks this cycle, which is the order things happen in: a
# colour runs out, gets restocked from the box above, then the box is emptied.
NEXT_STATE = {OUT: DISPLAY, DISPLAY: EXTRA, EXTRA: OUT}

STATE_SHORT = {OUT: 'OUT', DISPLAY: 'ON SHELF', EXTRA: 'EXTRA'}


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


class CaseStyle(models.Model):
    """A kind of case the shop stocks - plain silicone to begin with."""
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class ShelfColour(models.Model):
    """A colour the shop stocks cases in. Added freely by the shop."""
    name = models.CharField(max_length=40, unique=True)
    # Optional swatch so the grid can show the colour, not just its name.
    swatch = models.CharField(max_length=7, blank=True, help_text='e.g. #1f2937')
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class CaseStock(models.Model):
    """The state of one style x model x colour on the shelf."""
    style = models.ForeignKey(CaseStyle, on_delete=models.CASCADE,
                              related_name='stock_rows')
    model = models.ForeignKey('stock.DeviceModel', on_delete=models.CASCADE,
                              related_name='case_stock')
    colour = models.ForeignKey(ShelfColour, on_delete=models.CASCADE,
                               related_name='case_stock')
    state = models.CharField(max_length=8, choices=STOCK_STATES, default=OUT,
                             db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name='+')

    class Meta:
        unique_together = [('style', 'model', 'colour')]
        ordering = ['model', 'colour']

    def __str__(self):
        return f'{self.style} / {self.model} / {self.colour}: {self.state}'

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

    Used today to record that a case fits several handsets ("same as 14 Pro").
    It is a reminder for the person at the shelf and nothing more - the states
    of two models are not linked, so each is marked on its own. Real
    compatibility already exists as CompatibilityGroup and can drive this
    later without the notes being thrown away.
    """
    style = models.ForeignKey(CaseStyle, on_delete=models.CASCADE,
                              related_name='model_notes')
    model = models.ForeignKey('stock.DeviceModel', on_delete=models.CASCADE,
                              related_name='shelf_notes')
    note = models.CharField(max_length=120, blank=True)

    class Meta:
        unique_together = [('style', 'model')]

    def __str__(self):
        return f'{self.model}: {self.note}'
