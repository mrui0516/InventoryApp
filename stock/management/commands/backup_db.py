"""Consistent, rotated SQLite snapshot.

Runs on the PythonAnywhere server as a daily Scheduled Task (and can be run
locally too). Uses SQLite's online backup API against Django's *live* connection,
so the snapshot is consistent even if a web request is writing at the same moment
— and it works whether the DB is a file or the in-memory test database.

Writes ``<BASE_DIR>/backups/db-<YYYYMMDD-HHMMSS>.sqlite3`` and keeps the newest
``--keep`` snapshots (default 30), pruning older ones. The timestamped names sort
lexicographically, so newest-first is a plain reverse sort.
"""
import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Write a consistent, timestamped SQLite snapshot to backups/ and prune old ones."

    def add_arguments(self, parser):
        parser.add_argument('--keep', type=int, default=30,
                            help='How many snapshots to keep (default 30).')
        parser.add_argument('--dir', default=None,
                            help='Backup directory (default <BASE_DIR>/backups).')

    def handle(self, *args, **opts):
        if 'sqlite' not in settings.DATABASES['default']['ENGINE']:
            raise CommandError('backup_db only supports SQLite.')

        backup_dir = Path(opts['dir']) if opts['dir'] else Path(settings.BASE_DIR) / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)

        dest = backup_dir / f"db-{datetime.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"

        # Online backup of the live connection — consistent under concurrent writes.
        connection.ensure_connection()
        target = sqlite3.connect(str(dest))
        try:
            connection.connection.backup(target)
        finally:
            target.close()

        # Verify the snapshot is a healthy database before trusting it.
        chk = sqlite3.connect(str(dest))
        try:
            integrity = chk.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            chk.close()
        if integrity != 'ok':
            raise CommandError(f'Snapshot failed integrity check: {dest} ({integrity})')

        self.stdout.write(self.style.SUCCESS(
            f'Backup OK: {dest} ({dest.stat().st_size} bytes)'))

        # Prune: keep the newest --keep snapshots.
        snaps = sorted(backup_dir.glob('db-*.sqlite3'), key=lambda p: p.name, reverse=True)
        removed = 0
        for old in snaps[opts['keep']:]:
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
        if removed:
            self.stdout.write(f'Pruned {removed} old snapshot(s), kept {opts["keep"]}.')
