"""Bounded, source-fair review work; discovery timestamps never imply demand age."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .queue_store import load, locked, recover, commit, op
from .eligibility import instant


def report_window(started_at):
    day = instant(started_at).astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0)
    return day.isoformat(), (day + timedelta(days=1)).isoformat()


def create(root, execution_id, full_pending=False, report_window_start=None):
    if not execution_id or Path(execution_id).name != execution_id:
        raise ValueError('Invalid execution identifier')
    path = 'review-runs/' + execution_id + '.json'
    existing = load(root / path, {})
    if existing:
        return existing
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    history = load(root / 'review-plan-history.json', {'attempts': {}, 'runs': 0})
    groups = defaultdict(list)
    for row in pending:
        groups[row['source_id']].append(row)
    now = datetime.now(timezone.utc)
    recent = sorted([r for r in pending if r.get('first_seen') and
        instant(r['first_seen']) >= now - timedelta(hours=48)], key=lambda r: (
            history['attempts'].get(r['observation_id'], -1),
            -instant(r['first_seen']).timestamp(), r['source_id'], r['observation_id']))
    chosen = list(recent)
    for source in sorted(groups):
        # Least-recently assigned wins. A blocked item rotates behind untouched work.
        rows = sorted(groups[source], key=lambda r: (
            history['attempts'].get(r['observation_id'], -1),
            instant(r.get('first_seen')), r['observation_id']))
        untouched = [r for r in rows if r['observation_id'] not in history['attempts']]
        if untouched and history['runs'] % 2:
            row = max(untouched, key=lambda r: (instant(r.get('first_seen')), r['observation_id']))
        else:
            row = rows[0]
        if row['observation_id'] not in {r['observation_id'] for r in chosen}:
            chosen.append(row)
    if full_pending:
        chosen_ids = {row['observation_id'] for row in chosen}
        chosen.extend(row for row in sorted(pending, key=lambda r: (
            history['attempts'].get(r['observation_id'], -1), instant(r.get('first_seen')), r['observation_id']))
            if row['observation_id'] not in chosen_ids)
    selected = [{k: row.get(k) for k in ('observation_id', 'source_id',
        'canonical_path', 'first_seen', 'review_status')} for row in chosen]
    for row in chosen:
        history['attempts'][row['observation_id']] = history['runs']
    history['runs'] += 1
    plan = {'execution_id': execution_id, 'started_at': now.isoformat(),
            'review_scope': 'all_pending' if full_pending else 'recent_and_source_fair',
            'report_window_start': report_window_start or report_window(now.isoformat())[0],
            'pending_before': len(pending), 'sources_before': dict(Counter(r['source_id'] for r in pending)),
            'recent_discovery_count': len(recent),
            'selection': ('all pending, recent then source-fair backlog; discovery is not demand age' if full_pending
                else 'all discoveries within 48h, then source-fair backlog; discovery is not demand age'),
            'items': selected}
    commit(root, 'review-plan', [op(path, plan), op('review-plan-history.json', history)])
    return plan


def progress(root, plan):
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    pool = {r['observation_id']: r for r in load(root / 'opportunity-registry.json',
            {'observation_pool': []})['observation_pool']}
    rows = []
    for item in plan['items']:
        reviewed = pool.get(item['observation_id'], {})
        # blocked lacks reviewed_at: its reason is diagnostic, never completed review.
        complete = bool(reviewed.get('review_status') in {'accept', 'watch', 'exclude'} and (
            reviewed.get('reviewed_at') and instant(reviewed['reviewed_at']) >= instant(plan['started_at'])))
        rows.append({**item, 'completed': complete,
            'action': reviewed.get('review_status') if complete else 'unreviewed',
            'blocked_reason': plan.get('deferred', {}).get(item['observation_id'],
                reviewed.get('blocked_reason', ''))})
    return {'execution_id': plan['execution_id'], 'planned': len(rows),
            'recent_planned': plan.get('recent_discovery_count', 0),
            'completed': sum(r['completed'] for r in rows),
            'remaining': sum(not r['completed'] for r in rows), 'items': rows,
            'pending_after': len(pending),
            'pending_sources': dict(Counter(r['source_id'] for r in pending))}


def bind_collection(root, execution_id, exit_code, snapshots=None):
    """Append post-scan discoveries without changing this execution's reviewed work."""
    path = 'review-runs/' + execution_id + '.json'
    plan = load(root / path, {})
    if not plan:
        raise ValueError('Review plan does not exist')
    scan = load(root / 'latest-scan.json', {}) if snapshots is None else snapshots.get('scan', {})
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    selected = {r['observation_id'] for r in plan['items']}
    represented = {r['source_id'] for r in plan['items']}
    cutoff = instant(plan['started_at']) - timedelta(hours=48)
    added = []
    for row in sorted(pending, key=lambda r: (instant(r.get('first_seen')), r['observation_id'])):
        if row['observation_id'] in selected:
            continue
        if plan.get('review_scope') == 'all_pending' or instant(row.get('first_seen')) >= cutoff or row['source_id'] not in represented:
            added.append({k:row.get(k) for k in ('observation_id','source_id','canonical_path','first_seen','review_status')})
            selected.add(row['observation_id'])
            represented.add(row['source_id'])
    plan['items'].extend(added)
    plan['recent_discovery_count'] = sum(instant(r.get('first_seen')) >= cutoff for r in plan['items'])
    plan.update(collection_run_id=scan.get('run_id'), collection_exit=exit_code,
        collection_source_count=scan.get('source_count'), collection_recovery='finished',
        collection_scan=scan, supplemental_scans={name: (load(root / name, {}) if snapshots is None else snapshots.get(name, {})) for name in (
            'latest-template-scan.json', 'latest-pippit-template-scan.json')})
    if snapshots is not None:
        plan['collection_requested_template_tier'] = snapshots.get('requested_template_tier')
    commit(root, 'review-plan-collection', [op(path, plan)])
    return plan


def extend_pending(root, plan):
    """Keep a full review open to discovery arriving during long-running review."""
    if plan.get('review_scope') != 'all_pending':
        return plan
    selected = {row['observation_id'] for row in plan['items']}
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    added = [{k: row.get(k) for k in ('observation_id', 'source_id', 'canonical_path', 'first_seen', 'review_status')}
             for row in pending if row['observation_id'] not in selected]
    if added:
        plan['items'].extend(added)
        commit(root, 'review-plan-late-discoveries', [op('review-runs/' + plan['execution_id'] + '.json', plan)])
    return plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['create', 'progress', 'batch', 'defer'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--limit', type=int, default=16)
    parser.add_argument('--observation-id')
    parser.add_argument('--reason')
    parser.add_argument('--full-pending', action='store_true')
    args = parser.parse_args()
    with locked(args.root):
        recover(args.root)
        plan = create(args.root, args.execution_id, args.full_pending) if args.command == 'create' else load(
            args.root / 'review-runs' / (args.execution_id + '.json'), {})
        if not plan:
            raise ValueError('Review plan does not exist')
        plan = extend_pending(args.root, plan)
        if args.command == 'defer':
            if args.observation_id not in {r['observation_id'] for r in plan['items']} or not args.reason:
                raise ValueError('Deferral requires a planned observation and concrete reason')
            plan.setdefault('deferred', {})[args.observation_id] = args.reason
            commit(args.root, 'review-defer', [op('review-runs/' + args.execution_id + '.json', plan)])
            result = progress(args.root, plan)
        elif args.command == 'batch':
            if args.offset < 0 or args.limit < 1:
                raise ValueError('Invalid review batch bounds')
            state = progress(args.root, plan)
            unfinished = [row for row in state['items'] if not row['completed']
                and row['observation_id'] not in plan.get('deferred', {})]
            result = {'planned': state['planned'], 'remaining': state['remaining'],
                'ready_to_review': len(unfinished),
                'collection_notice': plan.get('collection_recovery'),
                'items': unfinished[args.offset:args.offset+args.limit]}
        else:
            result = plan if args.command == 'create' else progress(args.root, plan)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
