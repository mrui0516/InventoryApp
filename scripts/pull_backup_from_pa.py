#!/usr/bin/env python3
"""Download the newest PythonAnywhere DB snapshot to a local / USB folder.

Run this on the owner's Windows PC via Task Scheduler. Tick **"Run task as soon
as possible after a scheduled start is missed"** so a day the PC was off still
backs up on the next boot.

It lists ``.../InventoryApp/backups/`` on PythonAnywhere via the Files API, grabs
the newest ``db-*.sqlite3`` snapshot (produced by the server's daily
``manage.py backup_db`` task) and saves it to the USB. Stdlib only — no pip
install needed.

Config: a ``.pa_backup.ini`` next to this script (copy from
``.pa_backup.ini.example``) or ``PA_*`` environment variables. The API token is a
secret — the .ini is git-ignored; never commit it.
"""
import configparser
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def load_config():
    here = Path(__file__).resolve().parent
    cfg = configparser.ConfigParser()
    cfg.read(here / '.pa_backup.ini')
    sect = cfg['pa'] if cfg.has_section('pa') else {}

    def get(key, default=None):
        return os.environ.get('PA_' + key.upper()) or sect.get(key, default)

    conf = {
        'host': get('host', 'eu.pythonanywhere.com'),
        'username': get('username'),
        'token': get('token'),
        'dest': get('dest'),
        'keep': int(get('keep', '30') or 30),
    }
    conf['remote_dir'] = get('remote_dir', f"/home/{conf['username']}/InventoryApp/backups")
    missing = [k for k in ('username', 'token', 'dest') if not conf[k]]
    if missing:
        sys.exit(f"Missing config: {', '.join(missing)} "
                 f"(set them in scripts/.pa_backup.ini or PA_* env vars)")
    return conf


def _api(conf, path):
    url = f"https://{conf['host']}/api/v0/user/{conf['username']}/files/path{path}"
    req = urllib.request.Request(url, headers={'Authorization': f"Token {conf['token']}"})
    return urllib.request.urlopen(req, timeout=120)


def main():
    conf = load_config()
    dest = Path(conf['dest'])

    # USB unplugged / destination unavailable -> skip cleanly, never touch existing backups.
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.exit(f"Destination not available (USB unplugged?): {dest} — skipped. ({e})")

    # 1) List the server's backups dir and pick the newest snapshot.
    try:
        listing = json.load(_api(conf, conf['remote_dir'] + '/'))
    except urllib.error.HTTPError as e:
        sys.exit(f"PythonAnywhere API error listing {conf['remote_dir']}: {e}")
    snaps = sorted(
        name for name, meta in listing.items()
        if isinstance(meta, dict) and meta.get('type') == 'file'
        and name.startswith('db-') and name.endswith('.sqlite3')
    )
    if not snaps:
        sys.exit(f"No db-*.sqlite3 snapshots in {conf['remote_dir']} — did the PA backup task run?")
    newest = snaps[-1]
    out = dest / newest

    # 2) Download (skip if we already have this one). Write to .part then rename.
    if out.exists():
        print(f"Already have latest snapshot: {out}")
    else:
        tmp = dest / (newest + '.part')
        with _api(conf, conf['remote_dir'] + '/' + newest) as resp, open(tmp, 'wb') as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
        tmp.replace(out)
        print(f"Downloaded {newest} -> {out} ({out.stat().st_size} bytes)")

    # 3) Keep only the newest N locally.
    local = sorted(dest.glob('db-*.sqlite3'), key=lambda p: p.name, reverse=True)
    for old in local[conf['keep']:]:
        try:
            old.unlink()
        except OSError:
            pass


if __name__ == '__main__':
    main()
