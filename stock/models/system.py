"""Errors and warnings the app ran into, for Settings -> Audit -> Errors.

``stock.system_log.DatabaseHandler`` writes them. The same problem - the same
exception at the same line, or the same warning text - is one row whose count
and ``last_seen`` go up, so a bug hit a thousand times cannot flood the table.
A row marked resolved comes back the next time it happens.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class ErrorLog(models.Model):
    LEVEL_CHOICES = [('ERROR', 'Error'), ('WARNING', 'Warning')]

    fingerprint = models.CharField(max_length=40, unique=True)
    level = models.CharField(max_length=10, choices=LEVEL_CHOICES, db_index=True)
    source = models.CharField(max_length=120)          # logger name
    title = models.CharField(max_length=300)
    path = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name='+')
    detail = models.TextField(blank=True)              # traceback of the latest one
    count = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(default=timezone.now)
    last_seen = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-last_seen']

    def __str__(self):
        return f"{self.level} {self.title[:60]}"
