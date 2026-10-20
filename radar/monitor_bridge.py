"""Read committed local sitemap snapshots without crawling or recording native leads."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

NOTICE = ("Separate unevaluated sitemap candidate channel; URL text is candidate only. "
          "first_seen and lastmod are not publication dates. Domains are not independent businesses. "
          "Identity is exact monitor URL, not multilingual or semantic deduplication. "
          "Pending observations are historical backlog, not current-run additions.")


class BridgeError(Exception):
    pass


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed


def valid_url(value):
    return (isinstance(value, str) and not any(ord(c) < 32 for c in value)
            and urlsplit(value).scheme in {'http', 'https'} and bool(urlsplit(value).netloc))


class Inputs:
    def __init__(self, root):
        self.root = root.resolve()
        self.reads = {}

    def path(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise BridgeError('unsafe input path')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise BridgeError('unsafe input path')
        return path

    def read(self, relative):
        path = self.path(relative)
        raw = path.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        if path in self.reads and self.reads[path] != checksum:
            raise BridgeError('input changed during read')
        self.reads[path] = checksum
        return json.loads(raw)

    def verify(self):
        for path, checksum in self.reads.items():
            if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise BridgeError('input changed during read; baselines preserved')


def markdown(report):
    lines = ['# Sitemap monitor bridge', '', NOTICE, '',
             f"Status: {report['status']}; accepted {report['accepted_count']}/{report['source_count']} sources; "
             f"bridge URL additions: {report['addition_count']}; verified monitor-run additions: "
             f"{report['monitor_run_addition_count']} (reference only).",
             f"Monitor run: {report.get('monitor_run_id')}; generated: {report['generated_at']}", '',
             'No additions is not a no-new-terms finding; inspect coverage and errors.', '',
             '| Source | Status | Snapshot URLs | Bridge additions |', '|---|---|---:|---:|']
    for row in report['sources']:
        lines.append(f"| {row['source_id']} | {row['status']} | {row.get('url_count', 0)} | {len(row.get('additions', []))} |")
    for error in report['errors']:
        lines.append(f"\nError: {error}")
    for row in report['sources']:
        for entry in row.get('additions', []):
            lines.append(f"\n- {row['source_id']}: {json.dumps(entry['url'], ensure_ascii=False)}")
    return '\n'.join(lines) + '\n'


def _collect(inputs, previous, now, max_age_hours):
    cfg = inputs.read('monitor/config.json')
    scan = inputs.read('latest-scan.json')
    sources = cfg['sources']
    if not isinstance(sources, list) or not sources:
        raise BridgeError('missing configured sources')
    report = {'schema_version': 1, 'channel': 'unevaluated_sitemap_candidates',
              'generated_at': now.isoformat(), 'notice': NOTICE,
              'monitor_run_id': scan.get('run_id'), 'monitor_status': scan.get('status'),
              'source_count': len(sources), 'sources': [], 'errors': []}
    next_sources = dict(previous['sources'])
    ids = Counter(s.get('id') for s in sources if isinstance(s, dict) and isinstance(s.get('id'), str))
    running = scan.get('scan_phase') != 'complete' or scan.get('status') == 'running'
    run_id = scan.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', run_id):
        raise BridgeError('invalid run ID')
    prefix = f'batch-runs/{run_id}/'
    if scan.get('progress_file'):
        progress = inputs.read(scan['progress_file'])
        if progress.get('run_id') != run_id:
            raise BridgeError('progress run mismatch')
        running = running or progress.get('phase') != 'complete'
    receipts = scan.get('source_receipt_files', {})
    if not isinstance(receipts, dict):
        raise BridgeError('invalid receipt map')
    for index, source in enumerate(sources):
        sid = source.get('id') if isinstance(source, dict) else None
        safe = isinstance(sid, str) and re.fullmatch(r'[A-Za-z0-9_-]+', sid) and ids[sid] == 1
        row = {'source_id': sid if safe else f'invalid-source-{index}', 'status': 'malformed'}
        report['sources'].append(row)
        try:
            if not safe:
                raise BridgeError('invalid or duplicate source ID')
            if running:
                row['status'] = 'running'
                continue
            if sid not in receipts:
                row['status'] = 'not_checked'
                continue
            if not isinstance(receipts[sid], str) or not receipts[sid].startswith(prefix):
                raise BridgeError('receipt outside current run')
            receipt = inputs.read(receipts[sid])
            result = receipt.get('result', {})
            if receipt.get('source_id') != sid or result.get('id') != sid:
                raise BridgeError('receipt source mismatch')
            if receipt.get('status') != 'committed' or result.get('status') != 'ok':
                row['status'] = 'uncommitted'
                continue
            state = inputs.read(f'monitor/state/{sid}.json')
            if receipt.get('state_sha256') != digest(state):
                row['status'] = 'uncommitted'
                continue
            if state.get('source_id') != sid or state.get('observed_at') != result.get('observed_at'):
                raise BridgeError('state source/time mismatch')
            observed = instant(state['observed_at'])
            if not instant(scan['started_at']) <= observed <= instant(scan['observed_at']):
                raise BridgeError('snapshot outside monitor run')
            age = (now - observed).total_seconds() / 3600
            if age < 0 or age > max_age_hours:
                row['status'] = 'stale'
                continue
            scope = {key: sorted(set(source.get(key, []))) for key in
                     ('sitemap_urls', 'include_path_regex', 'exclude_path_regex')}
            scope_hash = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
            if state.get('scope_hash') != scope_hash:
                row['status'] = 'scope_mismatch'
                continue
            baseline = state['baseline']
            if not isinstance(baseline, dict) or not baseline:
                raise BridgeError('invalid snapshot map')
            entries = {}
            for url, entry in baseline.items():
                if not valid_url(url) or not isinstance(entry, dict) or entry.get('url') != url:
                    raise BridgeError('invalid snapshot URL entry')
                entries[url] = {k: entry[k] for k in ('url', 'first_seen', 'lastmod') if k in entry}
            old = previous['sources'].get(sid)
            if old and observed < instant(old['observed_at']):
                row['status'] = 'stale'
                continue
            status = 'baseline_created' if old is None else ('scope_reset' if old['scope_hash'] != scope_hash else 'ok')
            additions = sorted(set(entries) - set(old['urls'])) if status == 'ok' else []
            raw_added = result.get('added', [])
            if not isinstance(raw_added, list) or any(not isinstance(e, dict) or e.get('url') not in entries for e in raw_added):
                raise BridgeError('invalid receipt additions')
            row.update(status=status, observed_at=state['observed_at'], url_count=len(entries),
                       additions=[entries[url] for url in additions],
                       monitor_run_addition_count=len(raw_added),
                       monitor_run_addition_urls=sorted({e['url'] for e in raw_added}))
            next_sources[sid] = {'scope_hash': scope_hash, 'observed_at': state['observed_at'], 'urls': sorted(entries)}
        except (OSError, ValueError, TypeError, KeyError, AttributeError, BridgeError) as exc:
            row['status'] = 'malformed'
            row['error'] = type(exc).__name__ + ': invalid or unavailable source input'
    accepted = {'baseline_created', 'scope_reset', 'ok'}
    report['accepted_count'] = sum(r['status'] in accepted for r in report['sources'])
    report['addition_count'] = sum(len(r.get('additions', [])) for r in report['sources'])
    report['monitor_run_addition_count'] = sum(r.get('monitor_run_addition_count', 0) for r in report['sources'])
    report['status_counts'] = dict(Counter(r['status'] for r in report['sources']))
    for row in report['sources']:
        if row['status'] not in accepted:
            report['errors'].append(f"{row['source_id']}: {row['status']}; prior bridge snapshot preserved")
    report['status'] = 'ok' if not report['errors'] and scan.get('status') == 'ok' else 'degraded'
    return report, {'schema_version': 1, 'sources': next_sources}


def run_bridge(monitor_root, state_dir, output_dir, *, now=None, max_age_hours=36):
    root, state_dir, output_dir = map(lambda p: Path(p).resolve(), (monitor_root, state_dir, output_dir))
    if state_dir.is_relative_to(root) or output_dir.is_relative_to(root):
        raise BridgeError('bridge destinations must be outside monitor tree')
    if max_age_hours <= 0:
        raise BridgeError('freshness threshold must be positive')
    now = now or datetime.now(timezone.utc)
    state_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'bridge.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BridgeError('bridge busy; no outputs or baselines changed') from None
        inputs = Inputs(root)
        state_path = state_dir / 'baseline.json'
        previous = {'schema_version': 1, 'sources': {}}
        try:
            if state_path.exists():
                previous = json.loads(state_path.read_bytes())
                if previous['schema_version'] != 1 or not isinstance(previous['sources'], dict):
                    raise BridgeError('invalid bridge baseline')
                for sid, old in previous['sources'].items():
                    if not isinstance(old['scope_hash'], str) or not isinstance(old['urls'], list) or not all(valid_url(u) for u in old['urls']):
                        raise BridgeError('invalid bridge baseline')
                    instant(old['observed_at'])
            report, updated = _collect(inputs, previous, now, max_age_hours)
            inputs.verify()
        except (OSError, ValueError, TypeError, KeyError, AttributeError, BridgeError) as exc:
            report = {'schema_version': 1, 'channel': 'unevaluated_sitemap_candidates',
                      'generated_at': now.isoformat(), 'notice': NOTICE, 'status': 'degraded',
                      'source_count': 0, 'accepted_count': 0, 'addition_count': 0,
                      'monitor_run_addition_count': 0, 'sources': [],
                      'errors': [type(exc).__name__ + ': unavailable, corrupt, or changing input; bridge baselines preserved']}
            updated = None
        atomic_write(output_dir / 'sitemap-monitor.json', encoded(report))
        atomic_write(output_dir / 'sitemap-monitor.md', markdown(report))
        if updated is not None:
            atomic_write(state_path, encoded(updated))
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--monitor-root', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True, help='Bridge-only state directory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-age-hours', type=float, default=36)
    args = parser.parse_args(argv)
    try:
        report = run_bridge(args.monitor_root, args.state, args.output, max_age_hours=args.max_age_hours)
    except (BridgeError, OSError):
        print('Bridge unavailable or busy; no success claim.')
        return 2
    print(json.dumps({k: report[k] for k in ('status', 'source_count', 'accepted_count', 'addition_count')}))
    return 0 if report['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
