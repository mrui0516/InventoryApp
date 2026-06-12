from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import Sale
from .services import schedule_summary_recalc


@receiver(pre_save, sender=Sale)
def capture_previous_summary_day(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_summary_day = None
        return

    previous_date = Sale.objects.filter(pk=instance.pk).values_list("date", flat=True).first()
    instance._previous_summary_day = previous_date.date() if previous_date else None


@receiver(post_save, sender=Sale)
def update_summary_on_save(sender, instance, **kwargs):
    current_day = instance.date.date()
    previous_day = getattr(instance, "_previous_summary_day", None)

    schedule_summary_recalc(current_day)
    if previous_day and previous_day != current_day:
        schedule_summary_recalc(previous_day)


@receiver(post_delete, sender=Sale)
def update_summary_on_delete(sender, instance, **kwargs):
    schedule_summary_recalc(instance.date.date())
