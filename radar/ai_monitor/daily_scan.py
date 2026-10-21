"""Sequential sitemap scan; journal before baseline and pending updates."""
import argparse
import json
import re
from pathlib import Path
from .queue_store import monitor, load, locked, recover, commit, op, write, canonical_path, observation_id, merge_observations, domain, earliest_time, evidence_reference, digest
from . import review_pages
from .runtime import DEFAULT_ROOT
ROOT = DEFAULT_ROOT

def review_lane(source, evidence, migrated):
    if migrated: return 'migration'
    title = ' '.join(e.get('title', '') for e in evidence if e.get('valid')).lower()
    if source['id'] == 'pippit-ai':
        if re.search(r'\b(image|photo|picture|poster|logo)\b', title) and not re.search(r'\b(video|dance|animation|motion)\b', title):
            return 'static_image'
        context = ' '.join(e.get('title', '') + ' ' + e.get('summary', '') for e in evidence if e.get('valid'))
        if re.search(r'\b(video|dance|motion|animation|freestyle|lip.?sync|transition)\b', context, re.I):
            return 'video_candidate'
        return 'supplemental_evidence'
    if re.search(r'\b(api|sdk|cli|developer|integration|agent)\b', title) and source['id'] == 'higgsfield-ai': return 'developer_update'
    if re.search(r'\bugc\b', title): return 'secondary'
    if re.search(r'\b(image|photo|picture)\b', title) and not re.search(r'\b(video|dance|animation|motion)\b', title): return 'static_image'
    return 'secondary' if source['id'] == 'magichour-ai' else 'video_candidate'

def collect(source, options, previous, fetcher=monitor.fetch_bytes, evidence_fetcher=None, name_fetcher=None):
    current, maps = monitor.crawl(source, options, fetcher)
    next_state, result = monitor.reconcile(source, previous, current, monitor.utc_now())
    result['checked_maps'] = maps
    history = {}
    for url, stamp in (previous or {}).get('seen', {}).items():
        key = canonical_path(url, source); history[key] = earliest_time(history.get(key), stamp)
    groups = {}
    for entry in result['added']:
        key = canonical_path(entry['url'], source)
        if key not in history: groups.setdefault(key, []).append(entry)
    removed = [row['url'] for row in result['removed']]
    observations = []
    old_evidence = {url: evidence_reference(evidence_fetcher(url)) for url in removed} if groups and evidence_fetcher and not name_fetcher else {}
    # A removed page is evidence for a new entity only when its redirect or
    # canonical actually identifies that entity. Attaching the entire removed
    # library to every group is quadratic and contaminates unrelated reviews.
    result['migration_evidence_errors'] = [{'url': url, 'error': record.get('error', 'Invalid old page')}
                                          for url, record in old_evidence.items() if not record.get('valid')]
    for path, rows in groups.items():
        urls = sorted(row['url'] for row in rows); evidence = []; failures = []; migrated = []
        for old in removed:
            record = old_evidence.get(old, {'url': old, 'valid': False, 'error': 'Old page evidence unavailable'})
            targets = [record.get('final_url'), record.get('html_canonical')]
            if record.get('valid') and any(t and domain(t) == domain(urls[0]) and canonical_path(t, source) == path for t in targets):
                evidence.append(record)
                migrated.append(old)
        first = earliest_time(*(row['first_seen'] for row in rows))
        for url in urls:
            named = name_fetcher(url, result) if name_fetcher else None
            record = evidence_reference(named) if named else (evidence_reference(evidence_fetcher(url)) if evidence_fetcher else {'url': url, 'valid': False, 'error': 'Evidence not fetched'})
            evidence.append(record)
            if not record.get('valid'): failures.append({'url': url, 'error': record.get('error', 'Invalid new page')})
            else:
                for target in (record.get('final_url'), record.get('html_canonical')):
                    if target and domain(target) == domain(url) and canonical_path(target, source) in history:
                        migrated.append(target)
        if migrated:
            first = earliest_time(*(history.get(canonical_path(url, source), first) for url in migrated))
            for url in urls:
                next_state['seen'][url] = first
                next_state['baseline'][url]['first_seen'] = first
        observations.append({'observation_id': observation_id(source['id'], path), 'source_id': source['id'], 'source_name': source.get('name', source['id']), 'canonical_path': path, 'observed_at': result['observed_at'], 'first_seen': first, 'evidence_urls': urls, 'aliases': sorted(set(urls + migrated)), 'evidence': evidence, 'evidence_errors': failures, 'is_first_seen': not bool(migrated), 'classification': 'migration' if migrated else 'unreviewed', 'review_lane': review_lane(source, evidence, migrated), 'migrated_from': migrated, 'review_status': 'pending'})
    return next_state, result, observations

def run(root, selected=None, fetcher=monitor.fetch_bytes, evidence_fetcher=None, receipt_path=None):
    with locked(root):
        recover(root)
        config = load(root / 'monitor/config.json', {})
        sources = [s for s in config['sources'] if s['id'] in selected] if selected else [s for s in config['sources'] if s.get('enabled') or s['id'] in {'vswp-app', 'remakeyourmeme-com'}]
        if not sources: raise ValueError('No sources selected')
        if receipt_path and len(sources) != 1: raise ValueError('A worker receipt requires exactly one source')
        results, errors = [], []
        pending_count = None
        for source in sources:
            state_path = 'monitor/state/' + source['id'] + '.json'; previous = load(root / state_path, None)
            try:
                from .path_evidence import policy, collector
                name_fetcher = (lambda url, result: collector(root, source, url, result)) if policy(root) else None
                state, result, incoming = collect(source, config.get('fetch', {}), previous, fetcher, evidence_fetcher or (lambda u: review_pages.fetch(u, root)), name_fetcher)
            except Exception as error:
                result = {'id': source['id'], 'status': getattr(error, 'status', 'error'), 'error': str(error) or type(error.__cause__ or error).__name__, 'error_type': type(error.__cause__ or error).__name__, 'added': [], 'changed': [], 'removed': [], 'baseline': [], 'checked_maps': getattr(error, 'checked_maps', [])}
                errors.append({'source_id': source['id'], 'error': str(error)}); results.append(result); continue
            pending = load(root / 'pending-observations.json', {'observations': [], 'created_at': monitor.utc_now()})
            pending['observations'] = merge_observations(pending['observations'], incoming)
            pending_count = len(pending['observations'])
            try:
                operations = [op(state_path, state), op('pending-observations.json', pending)]
                if receipt_path:
                    operations.append(op(receipt_path, {'schema_version': 1, 'status': 'committed', 'source_id': source['id'], 'committed_at': monitor.utc_now(), 'state_sha256': digest(state), 'result': result}))
                commit(root, 'scan', operations, {'source_id': source['id'], 'next_state_operation': state_path, 'observations': incoming})
            except Exception as error:
                errors.append({'source_id': source['id'], 'error': 'Commit failed; replay required: ' + type(error).__name__ + ': ' + str(error)})
                result['error_type'] = type(error).__name__; result['status'] = 'error'; result['error'] = errors[-1]['error']; results.append(result); break
            results.append(result)
        # A successful worker still holds its large state/result/transaction graph.
        # Reloading pending here duplicates decoded Unicode and parsed objects,
        # and can exceed RLIMIT_AS *after* durable commit. Reuse the count we wrote.
        if pending_count is None:
            pending_count = len(load(root / 'pending-observations.json', {'observations': []})['observations'])
        stamp = monitor.utc_now().replace(':', '').replace('+', '_')
        status = 'ok' if not errors and len(results) == len(sources) else ('partial' if any(r['status'] == 'ok' for r in results) else 'failed')
        manifest = {'schema_version': 2, 'observed_at': monitor.utc_now(), 'status': status, 'source_count': len(sources), 'success_count': sum(r['status'] == 'ok' for r in results), 'errors': errors, 'pending_observations_count': pending_count, 'raw_result_file': 'monitor/results/' + stamp + '.json'}
        if receipt_path:
            # Success is durable in the same journal as baseline and pending. A
            # commit failure may still be replayable: never overwrite its receipt.
            if not (root / receipt_path).exists():
                write(root / receipt_path, {'schema_version': 1, 'status': 'failed', 'source_id': sources[0]['id'], 'result': results[0]})
        else:
            write(root / manifest['raw_result_file'], {'schema_version': 2, 'sources': results}); write(root / 'latest-scan.json', manifest)
        print(json.dumps(manifest, ensure_ascii=False))
        return 0 if status == 'ok' else 1

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root', type=Path, default=ROOT); parser.add_argument('--site', action='append'); parser.add_argument('--receipt')
    args = parser.parse_args(argv); return run(args.root, args.site, receipt_path=args.receipt)
if __name__ == '__main__': raise SystemExit(main())
