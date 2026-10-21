"""Hermes-reviewed report signals, separate from internal opportunity priority."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .eligibility import instant, publication_dates
from .path_evidence import verified
from .queue_store import load, locked, commit, op, digest, monitor


def window(plan):
    from .review_plan import report_window
    return (plan.get('report_window_start') or report_window(plan['started_at'])[0],
            plan.get('report_window_end') or datetime.now(timezone.utc).isoformat())


def record(root, ref, allowed=None):
    if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) and ref[k] for k in ('evidence_file', 'content_hash')):
        raise ValueError('Signal evidence needs a real file and hash')
    key = (ref.get('evidence_file'), ref.get('content_hash'))
    if allowed is not None and key not in allowed:
        raise ValueError('Signal evidence is not bound to the opportunity')
    path = (root / (key[0] or '')).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Signal evidence outside root')
    value = load(path, {})
    if not verified(root, value, key[1]):
        raise ValueError('Signal evidence is not hash verified')
    return value


def quoted(value, quote):
    return isinstance(quote, str) and len(quote.strip()) >= 8 and any(
        quote in text for text in ([value.get(field, '') for field in ('title', 'text', 'summary')]
                                  + [row.get('excerpt', '') for row in value.get('date_contexts', [])]))


def validate_supply(root, item, raw, at):
    from .queue_store import canonical_path
    ref = raw.get('supply_receipt', {})
    path = (root / ref.get('path', '')).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Supply receipt outside root')
    value = load(path, {})
    if not ref.get('sha256') or digest(value) != ref['sha256']:
        raise ValueError('Supply receipt hash mismatch')
    source = ref.get('source_id'); url = ref.get('url')
    matching = [row for row in item['valid_evidence'] if row.get('source_id') == source and url and
                canonical_path(row['url'], {'id':source}) == canonical_path(url, {'id':source})]
    cited={(row.get('evidence_file'),row.get('content_hash')) for row in raw.get('evidence_refs',[])}
    if not matching or not any((row.get('evidence_file'),row.get('content_hash')) in cited for row in matching):
        raise ValueError('Supply URL/source is not the reviewed subject')
    if path.relative_to(root.resolve()).as_posix().startswith('batch-runs/'):
        result = value.get('result', {})
        added = [row for row in result.get('added', []) if row.get('url') == url]
        if (value.get('status') != 'committed' or value.get('source_id') != source
                or result.get('status') != 'ok' or result.get('baseline_created') is not False
                or result.get('scope_reset') or instant(result.get('observed_at')) != at
                or not added or any(instant(row.get('first_seen')) != at for row in added)):
            raise ValueError('Supply requires a normal committed baseline addition, not inventory/restoration')
    elif path.relative_to(root.resolve()).as_posix().startswith('supply-deltas/'):
        if (value.get('source_id') != source or value.get('status') != 'ok'
                or value.get('baseline_created') is not False or url not in value.get('added_urls', [])
                or instant(value.get('observed_at')) != at
                or not instant(value.get('before_observed_at')) < at):
            raise ValueError('Supply requires a real persisted index delta after an established baseline')
    else:
        raise ValueError('Supply must reference a collector receipt or persisted index delta')


def validate(root, plan, item, raw):
    start, end = window(plan)
    if (not isinstance(raw, dict) or raw.get('event_id') != item['event_id']
            or raw.get('item_hash') != digest(item)
            or raw.get('execution_id') != plan.get('execution_id')
            or raw.get('window_start') != start
            or not isinstance(raw.get('window_end'), str)
            or not instant(start) < instant(raw.get('window_end')) <= instant(end)):
        raise ValueError('Report signal review does not match event, execution, hash or window')
    if (raw.get('decision') not in {'include', 'exclude'} or not isinstance(raw.get('reason'), str)
            or len(raw['reason'].strip()) < 24):
        raise ValueError('Report signal requires a real semantic include/exclude reason')
    allowed = {(r.get('evidence_file'), r.get('content_hash')) for r in item.get('valid_evidence', [])}
    refs = raw.get('evidence_refs')
    if not isinstance(refs, list) or not refs:
        raise ValueError('Report signal review requires bound evidence')
    records = [record(root, ref, allowed) for ref in refs]
    if raw['decision'] == 'exclude':
        return raw
    from .keyword_brief import public_text
    public_text(raw.get('signal_text'), 'signal_text', 300)
    if not isinstance(raw.get('signal_at'), str):
        raise ValueError('Signal requires a real evidence time')
    at = instant(raw['signal_at'])
    if not instant(start) <= at < instant(raw['window_end']):
        raise ValueError('Real signal time is outside the report window')
    current = records[0]
    if not quoted(current, raw.get('signal_quote')):
        raise ValueError('Signal needs an exact subject quote')
    kind = raw.get('signal_kind')
    if current.get('record_type') == 'discovery_name' and kind not in {'new_supply', 'first_cross_platform', 'recurrence'}:
        raise ValueError('Path/catalog names only support an observed supply signal')
    if kind == 'first_cross_platform':
        from .report_history import enabled, published
        from .eligibility import website
        sites = {website(value['url']) for value in records}
        cited = {(ref.get('evidence_file'), ref.get('content_hash')) for ref in refs}
        sources = {row.get('source_id') for row in item['valid_evidence']
                   if (row.get('evidence_file'), row.get('content_hash')) in cited and row.get('source_id')}
        if (not enabled(root, plan) or item['eligibility']['priority_tier'] != 2 or len(sites) < 2 or len(sources) < 2
                or (item.get('need_id') or item['id']) in published(root, plan)
                or at != instant(plan['started_at'])):
            raise ValueError('First cross-platform opportunity requires unreported two-platform evidence in its new workflow')
    elif kind == 'recurrence':
        from .report_history import enabled, published
        if (not enabled(root, plan) or (item.get('need_id') or item['id']) not in published(root, plan)
                or not raw.get('supply_receipt')):
            raise ValueError('Recurrence needs prior formal publication and an actual new source appearance')
        validate_supply(root, item, raw, at)
    elif kind == 'new_supply' and raw.get('supply_receipt'):
        validate_supply(root,item,raw,at)
    elif kind in {'publication', 'new_supply'}:
        dates = [instant(row['published_at']) for row in publication_dates(current) if not row.get('veto_only')]
        if at not in dates:
            raise ValueError('Publication/supply signal needs a verified publication time')
        if kind == 'publication' and not item['eligibility'].get('fresh_48h'):
            raise ValueError('Historical need cannot claim a new demand launch')
    elif kind in {'content_change', 'trend_change'}:
        before = record(root, raw.get('before_evidence', {}))
        if (before.get('url') != current.get('url')
                or before['content_hash'] == current['content_hash']
                or not all(isinstance(r.get('checked_at'), str) for r in (before,current))
                or not instant(start) <= instant(before.get('checked_at')) < at
                or at != instant(current.get('checked_at'))
                or not quoted(before, raw.get('before_quote'))
                or raw['before_quote'] == raw['signal_quote']):
            raise ValueError('Change signal needs same-subject before/after snapshots within window')
        # Hash changes alone are not trends. Hermes must identify the substantive
        # gameplay/supply or measured trend change, not navigation or promotion.
        if len(raw.get('substantive_change', '').strip()) < 32:
            raise ValueError('Change signal needs a concrete semantic comparison')
    else:
        raise ValueError('Unknown real report signal kind')
    return raw


def reviews(root, plan):
    return load(root / 'report-signals' / (plan['execution_id'] + '.json'), {}).get('reviews', {})


def supply_evidence(root, plan, items):
    """Index existing collector facts only; never manufacture missing pre-states."""
    start,end=map(instant,window(plan));result=[]
    sources={row.get('source_id') for item in items for row in item.get('valid_evidence',[])}
    for directory in sorted((root/'batch-runs').glob('*')):
        if not start.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat() <= directory.name[:10] <= end.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat():
            continue
        for source in sorted(sources):
            for path in (directory/'sources'/source).glob('*-receipt.json'):
                value=load(path,{});row=value.get('result',{})
                if (value.get('status')!='committed' or row.get('status')!='ok'
                        or row.get('baseline_created') is not False or row.get('scope_reset')
                        or not start <= instant(row['observed_at']) < end):
                    continue
                for entry in row.get('added',[]):
                    if instant(entry['first_seen'])==instant(row['observed_at']):
                        result.append({'path':path.relative_to(root).as_posix(),'sha256':digest(value),
                            'source_id':source,'url':entry['url'],'signal_at':row['observed_at']})
    for path in (root/'supply-deltas').glob('*.json'):
        value=load(path,{})
        if value.get('source_id') in sources and start<=instant(value['observed_at'])<end:
            result.extend({'path':path.relative_to(root).as_posix(),'sha256':digest(value),
                'source_id':value['source_id'],'url':url,'signal_at':value['observed_at']} for url in value['added_urls'])
    from .queue_store import canonical_path
    subjects={(row['source_id'],canonical_path(row['url'],{'id':row['source_id']}))
              for item in items for row in item.get('valid_evidence',[])}
    return [row for row in result if (row['source_id'],canonical_path(row['url'],{'id':row['source_id']})) in subjects]


def candidate(item):
    result={k:item.get(k) for k in ('event_id','need_id','id','event','need','first_seen','eligibility')}
    result['item_hash']=digest(item)
    result['valid_evidence']=[{k:row.get(k) for k in ('source_id','url','evidence_file','content_hash','checked_at','title')}
                              for row in item.get('valid_evidence',[])]
    return result


def submit(root, plan, values):
    from .keyword_brief import selected
    events = {item['event_id']: item for item in selected(root, plan)}
    existing = reviews(root, plan)
    seen = set()
    for raw in values:
        identifier = raw.get('event_id')
        if identifier in seen or identifier not in events:
            raise ValueError('Unknown or duplicate report signal event')
        seen.add(identifier)
        value = validate(root, plan, events[identifier], raw)
        existing[identifier] = dict(value, reviewed_at=monitor.utc_now())
    commit(root, 'report-signal-review', [op('report-signals/' + plan['execution_id'] + '.json',
        {'execution_id': plan['execution_id'], 'reviews': existing})])
    return {'saved': len(seen)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['candidates', 'submit', 'verify'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    with locked(args.root):
        plan = load(args.root / 'review-runs' / (args.execution_id + '.json'), {})
        if not plan:
            raise ValueError('Missing report plan')
        from .keyword_brief import selected, render
        if args.command == 'candidates':
            start, end = window(plan)
            items=selected(args.root,plan)
            keys={item.get('need_id') or item['id'] for item in items}
            pool=load(args.root/'opportunity-registry.json',{}).get('observation_pool',[])
            result = {'execution_id': args.execution_id, 'window_start': start, 'window_end': end,
                      'items': [candidate(item) for item in items],
                      'source_addition_evidence':supply_evidence(args.root,plan,items),
                      'original_observations':[{**{k:row.get(k) for k in ('observation_id','need_id','source_id',
                          'canonical_path','first_seen','observed_at','is_first_seen','migrated_from')},
                          'original_evidence':[{k:e.get(k) for k in ('evidence_file','content_hash','checked_at')}
                              for e in row.get('evidence',[])]}
                          for row in pool if row.get('need_id') in keys],
                      'existing_reviews': reviews(args.root, plan)}
            from .report_history import enabled
            if enabled(args.root, plan):
                from .report_history import published
                result['previously_reported_need_ids'] = sorted(published(args.root, plan))
                result['first_cross_platform_signal_at'] = plan['started_at']
                result['first_cross_platform_notice'] = 'First business inclusion only; never publication time or recent trend growth.'
        elif args.command == 'submit':
            result = submit(args.root, plan, load(args.input, []))
        else:
            text, result = render(args.root, plan)
            if result['missing']:
                raise ValueError('Report signal reviews or included briefs incomplete')
            # A quality hold is released only by real validated Hermes review;
            # native completion and delivery still use their existing full gates.
            if plan.get('delivery_hold') == 'report_signal_review_required':
                plan.pop('delivery_hold')
                commit(args.root, 'report-signal-hold-release', [op('review-runs/' + args.execution_id + '.json', plan)])
            result = dict(result, text=text)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
