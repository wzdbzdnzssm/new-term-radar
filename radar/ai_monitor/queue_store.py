"""Shared lock, identities and replayable file transactions; stdlib only."""
import contextlib
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, unquote

from . import run_monitor as monitor

LANGS = set('en ar bn br da de el es fa fi fr hi hu id it ja jp ko mr ms nb nl no pa ph pl pt pt-br pt-pt ru sv sw te th tr tw ur vi zh zh-hant zh-tw zhhant zh-cn'.split())

def load(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def digest(value):
    checksum = hashlib.sha256()
    if isinstance(value, str):
        checksum.update(value.encode())
    else:
        # Same default separators, key order, Unicode and floats as the original
        # dumps contract; avoid allocating the entire transaction and UTF-8 copy.
        for chunk in json.JSONEncoder(sort_keys=True, ensure_ascii=False).iterencode(value):
            checksum.update(chunk.encode())
    return checksum.hexdigest()

def earliest_time(*values):
    """Compare ISO instants in UTC; keep the original string and its precision.

    Legacy date-only/naive values use UTC, never the machine's local timezone.
    Equal instants retain the first supplied value, so old precision is preserved.
    """
    candidates = [value for value in values if value]
    if not candidates: return None
    if len(set(candidates)) == 1: return candidates[0]
    def instant(value):
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return min(candidates, key=instant)

def canonical_path(url, source=None):
    parts = [p for p in unquote(urlsplit(url).path).split('/') if p]
    # Only the documented language slots; never remove an arbitrary middle segment.
    positions = (source or {}).get('language_positions', ['root'])
    media = (urlsplit(url).hostname or '').removeprefix('www.') == 'media.io' or (source or {}).get('id') == 'media-io'
    if 'root' in positions and parts and parts[0].lower().replace('_', '-') in LANGS:
        parts.pop(0)
    if media and len(parts) > 1 and parts[0] == 'ai' and parts[1].lower() in LANGS:
        parts.pop(1)
    if (source or {}).get('id') == 'pippit-ai' and len(parts) == 2 and parts[0] == 'template-detail':
        identifier = re.search(r'(?:^|-)(\d{19})$', parts[1])
        if identifier:
            return '/template-detail/' + identifier.group(1)
    return '/' + '/'.join(parts)

def observation_id(source_id, path):
    return digest(source_id + '\n' + path)

def domain(url):
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')

@contextlib.contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'daily-scan.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

def write(path, value):
    monitor.atomic_json(path, value)
    if os.name != 'nt':
        descriptor = os.open(str(path.parent), os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)

def replay(root, journal_path, journal):
    if journal.get('status') == 'done':
        return
    for operation in journal['operations']:
        target = (root / operation['path']).resolve()
        if not target.is_relative_to(root.resolve()):
            raise ValueError('Journal target outside root')
        write(target, operation['value'])
    # Once every consumer file is durable, atomically replace the recovery payload
    # with a small audit receipt. A failure before this replacement leaves the full
    # prepared transaction available, so replay remains safe at every boundary.
    receipt = {'schema_version': 2, 'kind': journal['kind'], 'status': 'done', 'created_at': journal['created_at'],
               'transaction_hash': digest(journal), 'operation_receipts': [{'path': row['path'], 'sha256': digest(row['value'])} for row in journal['operations']]}
    write(journal_path, receipt)

def recover(root):
    for path in sorted((root / 'journals').glob('*.json')):
        journal = load(path, {})
        replay(root, path, journal)

def commit(root, kind, operations, details=None):
    journal = {'schema_version': 2, 'kind': kind, 'created_at': monitor.utc_now(), 'status': 'prepared', 'operations': operations, 'details': details or {}}
    key = digest(journal)
    path = root / 'journals' / (key + '.json')
    write(path, journal)  # Never mutate any consumer state until this is durable.
    replay(root, path, journal)
    return key

def op(path, value):
    return {'path': path, 'value': value}

def evidence_reference(record):
    """Keep small queue references; page files retain compact decision metadata.

    Refuse to discard an inline document that has no backing file. Legacy rows
    with a file already follow the same review contract as current fetches.
    """
    if any(key in record for key in ('text', 'raw_html')) and not record.get('evidence_file'):
        raise ValueError('Inline evidence requires a durable evidence_file before compaction')
    return {key: value for key, value in record.items() if key not in ('text', 'raw_html', 'date_contexts', 'reviewed_history', 'metadata_hash', 'publication_dates', 'ai_history_dates', 'content_verified')}

def merge_observations(existing, incoming):
    grouped = {}
    for row in existing + incoming:
        row = dict(row)
        if 'evidence' in row: row['evidence'] = [evidence_reference(record) for record in row['evidence']]
        path = canonical_path(row.get('evidence_urls', [row.get('canonical_path', '/')])[0], {'id': row['source_id']})
        row['canonical_path'] = path
        row['observation_id'] = observation_id(row['source_id'], path)
        row.setdefault('first_seen', row.get('first_seen_at', row.get('observed_at')))
        key = row['observation_id']
        if key not in grouped:
            grouped[key] = row
        else:
            old = grouped[key]
            old['evidence_urls'] = sorted(set(old.get('evidence_urls', []) + row.get('evidence_urls', [])))
            old['aliases'] = sorted(set(old.get('aliases', []) + row.get('aliases', []) + old['evidence_urls']))
            old['first_seen'] = earliest_time(old.get('first_seen'), row.get('first_seen'))
    return list(grouped.values())
