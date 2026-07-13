# stock/models/hr.py
"""HR domain: employee attendance (clock-in / clock-out) records."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class AttendanceRecord(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='attendance_records',
        db_index=True,
    )
    clock_in_at = models.DateTimeField(default=timezone.now, db_index=True)
    clock_out_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-clock_in_at', '-id']
        indexes = [
            models.Index(fields=['user', 'clock_in_at']),
            models.Index(fields=['user', 'clock_out_at']),
        ]

    def __str__(self):
        return f"{self.user} attendance {self.clock_in_at:%Y-%m-%d %H:%M}"

    @property
    def is_open(self):
        return self.clock_out_at is None

    @property
    def worked_duration(self):
        end_time = self.clock_out_at or timezone.now()
        duration = end_time - self.clock_in_at
        if duration.total_seconds() < 0:
            return timedelta()
        return duration
