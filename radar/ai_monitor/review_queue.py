"""Stable-ID semantic review, journaled registry and durable delivery outbox."""
import argparse
import json
import re
from .eligibility import qualify, POLICY
from .evidence_metadata import verified, history_matches, history_instant
from .path_evidence import verified as discovery_verified
from pathlib import Path
from .queue_store import monitor, load, write, locked, recover, commit, op, digest, domain, merge_observations, earliest_time
from .runtime import DEFAULT_ROOT
ROOT = DEFAULT_ROOT
CATEGORIES = {'video_template', 'static_image', 'developer_update', 'secondary', 'migration'}

def registry(root):
    value = load(root / 'opportunity-registry.json', {'schema_version': 2, 'opportunities': [], 'observation_pool': []})
    value['observation_pool'] = merge_observations(value.get('observation_pool', []), [])
    return value

def resolved(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()): raise ValueError('Evidence path outside root')
    return path

def evidence_index(root, urls):
    index = {url: [] for url in urls}
    for path in (root / 'page-evidence').glob('*.json'):
        record = load(path, {})
        if not isinstance(record, dict):
            if path.name == 'index.json': continue
            raise ValueError('Evidence record must be an object: ' + str(path))
        if record.get('url') in index:
            index[record['url']].append((path, record))
    return index


def validate_evidence(root, observation, submitted, indexed=None):
    allowed = set(observation.get('evidence_urls', []) + observation.get('aliases', []))
    rows = []
    for item in submitted:
        record = load(resolved(root, item['evidence_file']), {})
        expected = record.get('content_hash', digest(record.get('text', '')))
        if not discovery_verified(root, record):
            raise ValueError('Evidence file is not successful verified page metadata')
        if record.get('record_type') == 'discovery_name' and record.get('source_id') != observation['source_id']:
            raise ValueError('Discovery name belongs to another source')
        if item.get('content_hash') != expected:
            raise ValueError('Evidence content_hash mismatch')
        links = {record.get('url'), record.get('final_url'), record.get('html_canonical')}
        if record.get('url') not in allowed or item['url'] not in links:
            raise ValueError('Evidence URL does not belong to this observation')
        rows.append({'url': item['url'], 'source_id': observation['source_id'], 'domain': domain(record['url']), 'checked_at': record.get('checked_at'), 'evidence_file': item['evidence_file'], 'content_hash': expected, 'title': record.get('title'), 'media_types': record.get('media_types', []), 'summary': record.get('summary', record.get('text', '')[:1600]), 'final_url': record.get('final_url'), 'html_canonical': record.get('html_canonical')})
    if not rows: raise ValueError('Accept/watch/exclude decisions require evidence')
    # Preserve all fetched versions/supporting URLs, so selecting only a newer
    # page cannot hide an older publication for this same observation.
    covered = {(row['evidence_file'], row['content_hash']) for row in rows}
    history = ((path, load(path, {})) for path in (root / 'page-evidence').glob('*.json')) if indexed is None else (
        entry for url in allowed for entry in indexed.get(url, []))
    for path, record in history:
        # Legacy indexes are metadata arrays, not individual evidence records.
        if not isinstance(record, dict):
            if path.name == 'index.json': continue
            raise ValueError('Evidence record must be an object: ' + str(path))
        relative = path.relative_to(root).as_posix()
        body_hash = record.get('content_hash', digest(record.get('text', '')))
        if record.get('url') not in allowed or not discovery_verified(root, record): continue
        if (relative, body_hash) in covered: continue
        rows.append({'url': record['url'], 'source_id': observation['source_id'], 'domain': domain(record['url']), 'evidence_file': relative, 'content_hash': body_hash})
    return rows

def validate_history(root, evidence, submitted):
    rows = []
    allowed = {row['evidence_file']: row for row in evidence}
    for item in submitted:
        row = allowed.get(item.get('evidence_file'))
        if not row or row['content_hash'] != item.get('content_hash'): raise ValueError('Historical evidence must be hash-bound to reviewed evidence')
        record = load(resolved(root, row['evidence_file']), {})
        quote = item.get('original', '')
        offset = item.get('text_offset')
        if not verified(record, row['content_hash']) or not history_matches(record, item): raise ValueError('Historical evidence quote/locator mismatch')
        # Exact AI date token and explicit AI-demand scope are required. Human
        # semantic review binds this quote to the same stable need_id.
        from .eligibility import instant
        value = instant(item['published_at'])
        if item.get('scope') != 'same_ai_video_need' or item.get('date_text', '') not in quote or not item.get('date_text'): raise ValueError('Historical date must identify same AI video need')
        token = item['date_text']
        parsed = history_instant(token)
        if parsed != value: raise ValueError('Historical date does not match quote')
        rows.append({**item, 'url': row['url'], 'locator': 'text:' + str(offset), 'veto_only': True})
    return rows


def evidence_merge(old, new):
    # Keep every verified page observation; repeat submissions do not duplicate it.
    return list({(row['url'], row.get('content_hash'), row.get('evidence_file')): row for row in old + new}.values())

def count_sources(entry):
    urls = [row['url'] for row in entry.get('valid_evidence', [])]
    domains = sorted({row.get('domain') or domain(row['url']) for row in entry.get('valid_evidence', []) if row.get('domain') or domain(row['url'])})
    entry['distinct_domains'] = domains; entry['source_count'] = len(domains)
    entry['evidence_urls'] = sorted(set(entry.get('evidence_urls', []) + urls))

def migrate(root, patch=None):
    reg = registry(root)
    reg['schema_version'] = 2
    pending = load(root / 'pending-observations.json', {'observations': []})
    pending['observations'] = merge_observations(pending['observations'], [])
    for row in reg['observation_pool']:
        row.setdefault('first_seen', row.get('first_seen_at', row.get('observed_at')))
    for need in reg['opportunities']:
        need.setdefault('need_id', need['id'])
        need.setdefault('first_seen', need.get('first_seen_at'))
        need.setdefault('category', 'video_template')
        need.setdefault('demand_evidence', [])
        need.setdefault('next_test', need.get('recommendation', '待验证真实用户需求'))
        need.setdefault('external_coverage', [])
        need.setdefault('pool_sources', need.get('distinct_sources', []))
        # Preserve prior curated evidence, explicitly marked as legacy when not backed by a local file.
        evidence = list(need.get('valid_evidence', []))
        covered = {r['url'] for r in evidence}
        for url in need.get('evidence_urls', []):
            if url not in covered: evidence.append({'url': url, 'legacy_curated': True})
        need['valid_evidence'] = evidence
        count_sources(need)
        need.setdefault('announced_source_count', max(need.get('reported_source_count', 0), need['source_count'] if need.get('reported_at') else 0))
        need.setdefault('event_keys', ['new'] if need.get('reported_at') else [])
        if need.get('reported_at') and need['announced_source_count'] >= 2 and 'upgrade' not in need['event_keys']: need['event_keys'].append('upgrade')
    for need in reg['opportunities']:
        need.update((patch or {}).get('opportunities', {}).get(need['id'], {}))
    mapped = {need['id']: need for need in reg['opportunities']}
    for row in reg['observation_pool'] + pending['observations']:
        need = mapped.get(row.get('need_id'), {})
        if need.get('category'):
            row['classification'] = need['category']; row['review_lane'] = need['category']
        for key in ('known_outside_pool', 'migration_pending', 'external_coverage'):
            if key in need: row[key] = need[key]
        row['aliases'] = sorted(set(row.get('aliases', []) + row.get('evidence_urls', []) + need.get('url_aliases', [])))
        row.update((patch or {}).get('observations', {}).get(row['observation_id'], {}))
    commit(root, 'migration', [op('opportunity-registry.json', reg), op('pending-observations.json', pending)])
    return {'opportunities': len(reg['opportunities']), 'observations': len(reg['observation_pool']), 'pending': len(pending['observations'])}

def submit(root, decisions):
    reg = registry(root)
    pending = load(root / 'pending-observations.json', {'observations': []})
    pending['observations'] = merge_observations(pending['observations'], [])
    pool = {o['observation_id']: o for o in reg['observation_pool']}
    available = dict(pool)
    for observation in pending['observations']:
        key = observation['observation_id']; available[key] = {**available.get(key, {}), **observation}
    from .path_evidence import policy
    indexed = None
    if policy(root):
        urls = {url for decision in decisions if decision.get('action') != 'blocked'
                for row in [available.get(decision.get('observation_id'), {})]
                for url in row.get('evidence_urls', []) + row.get('aliases', [])}
        indexed = evidence_index(root, urls)
    needs = {n['id']: n for n in reg['opportunities']}
    before = {key: n.get('source_count', 0) for key, n in needs.items()}
    consumed, touched = set(), set()
    seen_decisions = set()
    for decision in decisions:
        identifier = decision['observation_id']
        if identifier in seen_decisions: raise ValueError('Duplicate decision for observation_id')
        seen_decisions.add(identifier)
        if identifier not in available: raise ValueError('Unknown observation_id: ' + identifier)
        observation = dict(available[identifier])
        action = decision['action']
        if action not in {'accept', 'watch', 'exclude', 'blocked'}: raise ValueError('Unknown action')
        if action == 'blocked':
            # A blocked decision is durable but never consumes/reindexes the observation.
            observation['review_status'] = 'blocked'; observation['blocked_reason'] = decision.get('reason', '')
            pool[identifier] = observation
            for row in pending['observations']:
                if row['observation_id'] == identifier:
                    row.update(review_status='blocked', blocked_reason=observation['blocked_reason'])
            continue
        category = decision.get('category')
        if category not in CATEGORIES: raise ValueError('A concrete category is required')
        if (observation.get('classification') == 'migration' or observation.get('migrated_from')) and category == 'video_template':
            raise ValueError('A URL migration cannot be promoted as a new video template; retain migration classification')
        evidence = validate_evidence(root, observation, decision.get('evidence', []), indexed)
        need_id = decision.get('need_id')
        if not need_id or not re.fullmatch(r'[a-zA-Z0-9_-]+', need_id): raise ValueError('Stable need_id required')
        if observation.get('need_id') and observation['need_id'] != need_id: raise ValueError('Existing observation need_id cannot be silently changed')
        observation.update(need_id=need_id, need=decision.get('need', observation.get('need', '')), classification=category, review_status=action, reviewed_at=monitor.utc_now(), valid_evidence=evidence_merge(observation.get('valid_evidence', []), evidence), demand_evidence=decision.get('demand_evidence', []), next_test=decision.get('next_test', '待验证'), priority=int(decision.get('priority', 2)))
        if observation['priority'] not in range(4): raise ValueError('priority must be 0..3')
        pool[identifier] = observation; consumed.add(identifier)
        if action == 'accept':
            if not observation['need']: raise ValueError('Need description required')
            need = needs.setdefault(need_id, {'id': need_id, 'need_id': need_id, 'need': observation['need'], 'first_seen': observation['first_seen'], 'first_seen_at': observation['first_seen'], 'pool_sources': [], 'valid_evidence': [], 'event_keys': [], 'announced_source_count': 0, 'reported_at': None})
            need['category'] = category; need['need'] = observation['need']
            need['valid_evidence'] = evidence_merge(need.get('valid_evidence', []), evidence)
            need['pool_sources'] = sorted(set(need.get('pool_sources', []) + [observation['source_id']]))
            need['distinct_sources'] = need['pool_sources']
            need['first_seen'] = earliest_time(need.get('first_seen'), observation.get('first_seen'))
            need['last_seen_at'] = observation.get('observed_at')
            need['demand_evidence'] = list({digest(x): x for x in need.get('demand_evidence', []) + decision.get('demand_evidence', [])}.values())
            need['historical_demand_evidence'] = evidence_merge(need.get('historical_demand_evidence', []), validate_history(root, evidence, decision.get('historical_demand_evidence', [])))
            need['external_coverage'] = list({digest(x): x for x in need.get('external_coverage', []) + decision.get('external_coverage', [])}.values())
            need['next_test'] = observation['next_test']; need['priority'] = observation['priority']
            for field in ('keywords', 'judgment', 'recommendation'):
                if field in decision: need[field] = decision[field]
            need['source_names'] = sorted(set(need.get('source_names', []) + [observation.get('source_name', observation['source_id'])]))
            count_sources(need); touched.add(need_id)
    events = []
    for key in sorted(touched):
        need = needs[key]
        for observation in pool.values():
            if observation.get('need_id') == key:
                need['valid_evidence'] = evidence_merge(need.get('valid_evidence', []), observation.get('valid_evidence', []))
        need['eligibility'] = qualify(root, need, monitor.utc_now()); count = need['eligibility']['source_count']; need['source_count'] = count; keys = need.setdefault('event_keys', [])
        if not need['eligibility']['eligible']: continue
        if need.get('category') != 'video_template' or need.get('migration_pending'): continue
        event = None
        if 'new' not in keys and not need.get('reported_at'): event = 'new'
        elif before.get(key, 0) < 2 <= count:
            if (need.get('reported_at') or 'new' in need.get('featured_event_types', [])) and 'upgrade' not in keys:
                event = 'upgrade'
            elif 'new:multisource' not in keys:
                # An archived single-source item was never featured. Its new evidence
                # earns a fresh ranking opportunity, not a claim of a delivered upgrade.
                event = 'new'; keys.append('new:multisource')
        if event:
            keys.append(event); need['announced_source_count'] = max(count, need.get('announced_source_count', 0))
            events.append({'event_id': digest(key + ':' + event + ':' + str(count)), 'event': event, **need})
    from .eligibility import instant
    events.sort(key=lambda n: (n['eligibility']['priority_tier'], -instant(n['eligibility']['earliest_known_published_at']).timestamp() if n['eligibility']['fresh_48h'] else 0, -n['source_count'], n.get('priority', 2), n['id']))
    for event in events:
        need = needs[event['id']]
        need['featured_event_types'] = sorted(set(need.get('featured_event_types', []) + [event['event']]))
    reg['opportunities'] = list(needs.values()); reg['observation_pool'] = list(pool.values()); reg['last_review_at'] = monitor.utc_now()
    pending['observations'] = [o for o in pending['observations'] if o['observation_id'] not in consumed]
    latest = load(root / 'latest-scan.json', {})
    errors = latest.get('errors', [])
    # Do not duplicate an error-only event if the same manifest has already been queued.
    error_key = digest(errors) if errors else None
    new_errors = errors if error_key and reg.get('last_error_key') != error_key else []
    operations = []
    outbox_id = None
    if events or new_errors:
        text = render(events, new_errors)
        outbox_id = digest({'events': [x['event_id'] for x in events], 'error_key': error_key})
        envelope = {'schema_version': 2, 'policy_version': POLICY, 'outbox_id': outbox_id, 'created_at': monitor.utc_now(), 'status': 'awaiting_receipt', 'text': text, 'text_hash': digest(text), 'featured_ids': [x['event_id'] for x in events], 'items': [dict(x, placement='featured') for x in events], 'errors': new_errors}
        operations.append(op('outbox/' + outbox_id + '.json', envelope))
        if new_errors: reg['last_error_key'] = error_key
    operations.extend([op('opportunity-registry.json', reg), op('pending-observations.json', pending)])
    commit(root, 'review', operations, {'decisions': decisions, 'outbox_id': outbox_id})
    materialize(root)
    return {'outbox_id': outbox_id, 'events': len(events), 'consumed': len(consumed), 'pending': len(pending['observations'])}

def render(items, errors):
    lines = ['AI视频需求：近48小时新需求优先，其次跨站热需求，其余观察']
    for index, item in enumerate(items):
        if index == 3: lines.append('其余候选（按同一优先级排序）：')
        eligibility = item.get('eligibility', {})
        label = {1: '近48小时新需求', 2: '跨站热需求', 3: '非新非热观察'}.get(eligibility.get('priority_tier'), '观察')
        if eligibility.get('observed_new_supply') and not eligibility.get('fresh_48h'):
            label = '新增页面或模板条目'
        keywords = item.get('keywords', [])
        if isinstance(keywords, list): keywords = '；'.join(keywords)
        external = item.get('external_coverage', [])
        external_urls = ' '.join(x.get('url', '') if isinstance(x, dict) else str(x) for x in external)
        demand = item.get('demand_evidence', [])
        lines.extend([f"{label}｜池内{item['source_count']}站｜{item['need']}",
            '关键词：' + (keywords or '待补充'),
            '来源平台：' + '、'.join(item.get('source_names', item.get('pool_sources', []))),
            '最早可核验发布：' + str(item.get('eligibility', {}).get('earliest_known_published_at')),
            '判断：' + item.get('judgment', '；'.join(eligibility.get('reasons', [])) or '已核验近48小时发布；真实搜索量未验证。'),
            '新旧状态：' + ('近48小时新' if eligibility.get('fresh_48h') else ('日期未知' if not eligibility.get('earliest_known_published_at') else '历史需求/非近48小时')),
            '外部旧覆盖：' + (external_urls or '未核实；不并入池内来源数'),
            '真实需求证据：' + (json.dumps(demand, ensure_ascii=False) if demand else '搜索量/播放量未验证，不作为推荐门槛；跨源热按至少2个不同网站出现同一需求判定'),
            '下一步：' + item.get('next_test', '待验证'), '证据：' + ' '.join(item.get('evidence_urls', []))])
    if errors: lines.append('采集异常：' + json.dumps(errors, ensure_ascii=False))
    return '\n'.join(lines)

def materialize(root):
    # These are derived artifacts. Failure is recoverable from the durable outbox.
    for path in (root / 'outbox').glob('*.json'):
        record = load(path, {})
        if record.get('status') != 'awaiting_receipt' or not outbox_allowed(root, record): continue
        target = root / 'briefings' / record['outbox_id']; target.parent.mkdir(parents=True, exist_ok=True)
        import os, tempfile
        fd, temp = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(record['text']); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, target.with_suffix('.txt'))
        finally:
            if os.path.exists(temp): os.unlink(temp)
        write(target.with_suffix('.items.json'), {'outbox_id': record['outbox_id'], 'text_hash': record['text_hash'], 'items': record['items'], 'errors': record['errors']})

def outbox_allowed(root, record):
    # Never mutate a historical payload or hash. Re-evaluate even retrieved records.
    if record.get('status') != 'awaiting_receipt' or record.get('policy_version') != POLICY: return False
    needs = {n['id']: n for n in registry(root)['opportunities']}
    for item in record.get('items', []):
        current = qualify(root, needs.get(item['id'], item), monitor.utc_now())
        if not current['eligible'] or current['priority_tier'] != item.get('eligibility', {}).get('priority_tier'): return False
    return True


def eligibility_report(root):
    return {'policy_version': POLICY, 'opportunities': [{'id': n['id'], **qualify(root, n, monitor.utc_now())} for n in registry(root)['opportunities']], 'outboxes': [{'outbox_id': r.get('outbox_id'), 'send_allowed': outbox_allowed(root, r)} for r in [load(p, {}) for p in sorted((root / 'outbox').glob('*.json'))]]}


def outboxes(root, identifier=None):
    materialize(root)
    records = [load(p, {}) for p in sorted((root / 'outbox').glob('*.json'))]
    blocked_ids = [r['outbox_id'] for r in records if not outbox_allowed(root, r) and r.get('status') == 'awaiting_receipt']
    records = [r for r in records if outbox_allowed(root, r)]
    records = [r for r in records if (r['outbox_id'] == identifier if identifier else r['status'] == 'awaiting_receipt')]
    for record in records:
        record['newly_prepared'] = not bool(record.get('first_retrieved_at'))
        if identifier and not record.get('first_retrieved_at'):
            record['first_retrieved_at'] = monitor.utc_now()
            commit(root, 'outbox_retrieval', [op('outbox/' + record['outbox_id'] + '.json', record)])
    errors = load(root / 'latest-scan.json', {}).get('errors', [])
    # Listing is not a send/replay command. Only an explicit ID exposes exact text.
    if not identifier:
        records = [{k: r[k] for k in ('outbox_id', 'status', 'text_hash', 'created_at', 'featured_ids', 'newly_prepared')} for r in records]
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    signal = 'COLLECTION_ERROR' if errors else ('AWAITING_RECEIPT' if records else ('REVIEW_PENDING' if pending else 'SILENT'))
    return {'outboxes': records, 'blocked_outbox_ids': blocked_ids, 'policy_version': POLICY, 'signal': signal, 'auto_resend': False, 'pending_review_count': len(pending), 'collection_errors': errors}

def ack(root, identifier, text_hash, receipt, verifier=None):
    path = root / 'outbox' / (identifier + '.json'); record = load(path, {})
    required = {'delivery_id', 'platform_message_id', 'verification_source', 'channel', 'destination', 'sent_at', 'status', 'outbox_id', 'text_hash'}
    if not required.issubset(receipt) or any(not receipt[k] for k in required): raise ValueError('Structured transport receipt required')
    if receipt['status'] != 'delivered' or receipt['outbox_id'] != identifier or receipt['text_hash'] != text_hash or record.get('text_hash') != text_hash: raise ValueError('Receipt/outbox/hash mismatch')
    # A receipt supplied by an LLM/operator is not transport proof. The integration must
    # query a real provider or its trustworthy raw receipt store through this boundary.
    if verifier is None: raise ValueError('No verified platform receipt adapter; remains awaiting_receipt')
    proof = verifier(receipt)
    if not isinstance(proof, dict) or any(proof.get(k) != receipt[k] for k in required): raise ValueError('Provider did not verify exact message/hash/destination receipt')
    if record.get('status') == 'delivered': return {'status': 'already_acknowledged'}
    if record.get('status') != 'awaiting_receipt': raise ValueError('Outbox is not awaiting receipt')
    record.update(status='delivered', receipt=receipt, delivered_at=receipt['sent_at'])
    reg = registry(root)
    for entry in reg['opportunities']:
        for event in record['items']:
            if event['id'] == entry['id'] and event['event_id'] in record['featured_ids']:
                entry['reported_at'] = receipt['sent_at']; entry['reported_source_count'] = event['source_count']
    commit(root, 'ack', [op(str(path.relative_to(root)), record), op('opportunity-registry.json', reg)])
    return {'status': 'delivered', 'outbox_id': identifier}

def ack_base(root, identifier, receipt, verifier):
    """A Base row is a separate durable publication channel, never a message receipt."""
    path = root / 'outbox' / (identifier + '.json')
    record = load(path, {})
    required = {'channel', 'base_token', 'table_id', 'record_ids', 'outbox_id',
                'text_hash', 'verified_at', 'status'}
    if not required.issubset(receipt) or any(not receipt[key] for key in required):
        raise ValueError('Confirmed Base receipt required')
    if (receipt['channel'] != 'feishu_bitable' or receipt['status'] != 'confirmed_base'
            or receipt['outbox_id'] != identifier or receipt['text_hash'] != record.get('text_hash')
            or digest(record.get('text', '')) != receipt['text_hash']):
        raise ValueError('Base receipt/outbox/hash mismatch')
    if verifier is None or verifier(receipt, record['text']) is not True:
        raise ValueError('Base provider did not verify exact persisted report')
    if record.get('status') == 'delivered':
        return {'status': 'already_acknowledged'}
    if record.get('status') != 'awaiting_receipt':
        raise ValueError('Outbox is not awaiting receipt')
    record.update(status='delivered', receipt=receipt, delivered_at=receipt['verified_at'])
    reg = registry(root)
    for entry in reg['opportunities']:
        for event in record['items']:
            if event['id'] == entry['id'] and event['event_id'] in record['featured_ids']:
                entry['reported_at'] = receipt['verified_at']
                entry['reported_source_count'] = event['source_count']
    commit(root, 'ack_base', [op(str(path.relative_to(root)), record),
                            op('opportunity-registry.json', reg)])
    return {'status': 'delivered', 'outbox_id': identifier, 'channel': 'feishu_bitable'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='command', required=True)
    migration = sub.add_parser('migrate'); migration.add_argument('--patch', type=Path)
    sub.add_parser('export')
    sub.add_parser('eligibility')
    submission = sub.add_parser('submit'); submission.add_argument('--decisions', type=Path, required=True)
    outbox = sub.add_parser('outbox'); outbox.add_argument('--id')
    receipt = sub.add_parser('ack'); receipt.add_argument('--id', required=True); receipt.add_argument('--text-hash', required=True); receipt.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args(argv)
    with locked(args.root):
        recover(args.root)
        if args.command == 'migrate': result = migrate(args.root, load(args.patch, {}) if args.patch else None)
        elif args.command == 'submit': result = submit(args.root, load(args.decisions, {})['decisions'])
        elif args.command == 'eligibility': result = eligibility_report(args.root)
        elif args.command == 'outbox': result = outboxes(args.root, args.id)
        elif args.command == 'ack': result = ack(args.root, args.id, args.text_hash, load(args.receipt, {}))
        else:
            result = load(args.root / 'pending-observations.json', {'observations': []})
            result['template_candidates'] = load(args.root / 'video-template-candidates.json', {'entities': {}})['entities']
            result['template_coverage'] = load(args.root / 'latest-template-scan.json', {})
            result['pippit_template_coverage'] = load(args.root / 'latest-pippit-template-scan.json', {})
        print(json.dumps(result, ensure_ascii=False))
    return 0
if __name__ == '__main__': raise SystemExit(main())
