# stock/attendance_models.py
"""Backwards-compatibility shim.

``AttendanceRecord`` now lives in :mod:`stock.models.hr` (HR domain). This
re-export keeps any lingering ``from stock.attendance_models import ...``
imports working. New code should import from ``stock.models`` directly.
"""
from .models.hr import AttendanceRecord

__all__ = ['AttendanceRecord']
