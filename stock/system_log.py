"""Logging handler that keeps errors and warnings in the database (``ErrorLog``),
so they can be read in Settings -> Audit -> Errors instead of only in the
host's error log file.

Configured in ``settings.LOGGING`` for crashes (``django.request``), security
warnings (``django.security``: stale forms, unknown hosts) and the app's own
loggers (``stock.*``: Cloudinary, Shopify, signals).
"""
import hashlib
import logging
import threading
import time
import traceback
from datetime import timedelta

KEEP_DAYS = 90
_busy = threading.local()

# A warning repeated within this many seconds is counted, not written. A
# Shopify job retrying in a loop logged the same line thousands of times, and
# each write competed with the till for SQLite's single write lock
# (2026-11-22). Errors, failed logins and security warnings are always written.
REPEAT_SECONDS = 60
UNTHROTTLED = ('stock.auth', 'django.security')
_recent = {}                 # fingerprint -> [monotonic time written, repeats not yet written]
_recent_lock = threading.Lock()


def _held_back(fingerprint, level, source):
    """``(skip, repeats)``: whether to skip this write, and how many earlier
    repeats to add to the count when it is written."""
    if level != 'WARNING' or source.startswith(UNTHROTTLED):
        return False, 0
    now = time.monotonic()
    with _recent_lock:
        entry = _recent.get(fingerprint)
        if entry and now - entry[0] < REPEAT_SECONDS:
            entry[1] += 1
            return True, 0
        if len(_recent) > 500:
            _recent.clear()
        _recent[fingerprint] = [now, 0]
        return False, entry[1] if entry else 0


def _describe(record):
    """``(title, key, detail)``: a one-line title, what makes two records the
    same problem, and the full text to read later."""
    exc = record.exc_info
    if exc and exc[0]:
        name = exc[0].__name__
        title = f"{name}: {exc[1]}" if str(exc[1]) else name
        frames = traceback.extract_tb(exc[2]) if exc[2] else []
        # the deepest frame in our own code says where it broke
        from django.conf import settings
        base = str(settings.BASE_DIR)
        ours = [f for f in frames if f.filename.startswith(base) and 'site-packages' not in f.filename]
        where = (ours or frames or [None])[-1]
        key = f"{name}|{where.filename}:{where.lineno}" if where else name
        detail = ''.join(traceback.format_exception(*exc))
        if record.name == 'django.request':
            title = f"{title} ({record.getMessage().split(':', 1)[-1].strip()})"
    else:
        message = record.getMessage()
        title, key, detail = message, f"{record.name}|{record.msg}", message
    first = (title.strip().splitlines() or [record.name])[0]
    return first[:300], key, detail[:20000]


def _store(record):
    from django.db import connection, transaction
    from django.db.models import F
    from django.utils import timezone
    from .models import ErrorLog

    if connection.in_atomic_block and connection.needs_rollback:
        return          # the request's transaction is broken; stderr still has it
    title, key, detail = _describe(record)
    level = 'ERROR' if record.levelno >= logging.ERROR else 'WARNING'
    fingerprint = hashlib.sha1(f"{level}|{key}".encode('utf-8', 'replace')).hexdigest()
    skip, repeats = _held_back(fingerprint, level, record.name)
    if skip:
        # A read, not a write; the row may be gone if its transaction rolled back.
        if ErrorLog.objects.filter(fingerprint=fingerprint).exists():
            return
        with _recent_lock:
            _recent[fingerprint] = [time.monotonic(), 0]
    request = getattr(record, 'request', None)
    path = (getattr(request, 'path', '') or '')[:300]
    user = getattr(request, 'user', None)
    user = user if getattr(user, 'is_authenticated', False) else None
    now = timezone.now()
    with transaction.atomic():
        changes = {'count': F('count') + 1 + repeats, 'last_seen': now, 'resolved_at': None, 'title': title}
        if detail:
            changes['detail'] = detail
        if path:
            changes['path'] = path
        if user:
            changes['user'] = user
        if not ErrorLog.objects.filter(fingerprint=fingerprint).update(**changes):
            ErrorLog.objects.create(fingerprint=fingerprint, level=level, source=record.name[:120],
                                    title=title, path=path, user=user, detail=detail,
                                    first_seen=now, last_seen=now)
            ErrorLog.objects.filter(last_seen__lt=now - timedelta(days=KEEP_DAYS)).delete()


class DatabaseHandler(logging.Handler):
    def emit(self, record):
        if getattr(_busy, 'on', False) or record.name.startswith('django.db'):
            return
        _busy.on = True
        try:
            _store(record)
        except Exception:
            # Recording a problem must never become a second problem: the
            # record still reaches stderr through the console handler.
            pass
        finally:
            _busy.on = False


def record_failed_login(sender, credentials, request=None, **kwargs):
    """A wrong password: one row per username tried, counting the attempts.
    Shown on Settings -> Audit -> Accounts, not among the errors."""
    username = (credentials or {}).get('username') or '?'
    logging.getLogger('stock.auth').warning('Failed login: %s' % username[:80],
                                            extra={'request': request})
