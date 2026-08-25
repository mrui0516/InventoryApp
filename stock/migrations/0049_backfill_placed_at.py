"""Give placed_at to the orders where it is a fact, and only those.

Two groups qualify:

* Orders still awaiting receipt. They were created through the pending flow,
  which happens when the order is put to the supplier, so created_at *is* when
  it was placed. Without this they would land with nothing to measure against.
* Orders already received after a real wait (received_at > created_at). These
  are the ones the supplier page is already reporting; leaving them out would
  wipe the averages the shop can see today.

Everything else keeps placed_at NULL on purpose: 156 orders carry
received_at == created_at and 40 carry no receipt time at all, so there is
nothing to measure and no honest way to invent it.
"""
from django.db import migrations
from django.db.models import F


def backfill(apps, schema_editor):
    InboundOrder = apps.get_model('stock', 'InboundOrder')

    InboundOrder.objects.filter(
        status='pending_receipt', placed_at__isnull=True,
    ).update(placed_at=F('created_at'))

    InboundOrder.objects.filter(
        status='received', placed_at__isnull=True,
        received_at__isnull=False, received_at__gt=F('created_at'),
    ).update(placed_at=F('created_at'))


def clear(apps, schema_editor):
    apps.get_model('stock', 'InboundOrder').objects.update(placed_at=None)


class Migration(migrations.Migration):
    dependencies = [('stock', '0048_inboundorder_placed_at')]
    operations = [migrations.RunPython(backfill, clear)]
