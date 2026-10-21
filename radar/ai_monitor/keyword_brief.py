"""Evidence-bound keyword briefs; immutable opportunity outboxes stay untouched."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .eligibility import instant, website
from .evidence_metadata import verified
from .queue_store import load, locked, commit, op, digest, monitor
from .runtime import DEFAULT_ROOT

EMPTY = '本次暂无可发布的新趋势词'
PUBLIC_UNSAFE = re.compile(r'https?://|www\.|(?:^|\s)/[\w-]+/|[A-Za-z]:[\\/]|[0-9a-f]{32,}|execution[_ -]?id|outbox|page-evidence|CRON_FAILURE|SILENT|(?:扫描|采集)(?:失败|状态|覆盖|完成|数量)|队列(?:数量|状态|\s*\d)|(?:已审|待审|审核)\s*\d+条|(?:运行|系统|采集)故障|运行(?:失败|状态|次数)|执行(?:ID|标识)|回执|哈希|sha256', re.I)
UNSUPPORTED = re.compile(r'\bfree\b|no[ -]watermark|no[ -]sign[ -]?up|免费|无水印|免注册', re.I)


def public_text(value, field, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Invalid keyword brief field: ' + field)
    if '\n' in value or PUBLIC_UNSAFE.search(value) or (field != 'platform' and re.search(
            r'\b[\w-]+\.(?:com|net|io|ai|org)\b|(?:^|\s)[\w.-]+/[\w/-]+', value, re.I)):
        raise ValueError('Non-business content in keyword brief: ' + field)
    if value.strip() in {'待补充', '未知', '待验证', 'N/A'}:
        raise ValueError('Empty keyword brief placeholder: ' + field)
    return value.strip()


def selected(root, plan, blocked_ids=()):
    """All newly featured P1/P2 needs in this run, without a result-count cap."""
    needs = {}
    if not plan:
        return []
    from .review_plan import report_window
    start, _ = report_window(plan['started_at'])
    start = instant(plan.get('report_window_start', start))
    end = instant(plan.get('report_window_end', datetime.now(timezone.utc).isoformat()))
    for path in sorted((root / 'outbox').glob('*.json')):
        box = load(path, {})
        if box.get('outbox_id') in blocked_ids or not start <= instant(box.get('created_at')) < end:
            continue
        for item in box.get('items', []):
            eligibility = item.get('eligibility', {})
            tier = eligibility.get('priority_tier', 3)
            if not eligibility.get('eligible') or tier not in (1, 2) or item.get('event') not in {'new', 'upgrade'}:
                continue
            key = item.get('need_id') or item['id']
            candidate = (tier, -instant(box['created_at']).timestamp(), item['event_id'])
            if key not in needs or candidate < needs[key][0]:
                needs[key] = (candidate, item)
    return [entry[1] for entry in sorted(needs.values(), key=lambda entry: entry[0])]


def validate(root, item, raw):
    if not isinstance(raw, dict) or raw.get('event_id') != item['event_id']:
        raise ValueError('Keyword brief must identify the real opportunity event')
    main = public_text(raw.get('main_term'), 'main_term', 140)
    if main == (item.get('need_id') or item.get('id')):
        raise ValueError('Main term must be a source name, not a need identifier')
    result = {'event_id': item['event_id'], 'need_id': item.get('need_id') or item['id'],
              'item_hash': digest(item), 'main_term': main}
    for field in ('related_terms', 'long_tail_terms'):
        values = raw.get(field)
        if not isinstance(values, list) or not 1 <= len(values) <= 8:
            raise ValueError('Keyword brief requires ' + field)
        result[field] = list(dict.fromkeys(public_text(value, field, 180) for value in values))
        if any(UNSUPPORTED.search(value) for value in result[field]):
            raise ValueError('Unsupported capability promise in keyword suggestions')
    result['seo_advice'] = public_text(raw.get('seo_advice'), 'seo_advice', 500)
    if (len(result['seo_advice']) < 24 or UNSUPPORTED.search(result['seo_advice'])
            or main.casefold() not in result['seo_advice'].casefold()):
        raise ValueError('Specific, supportable SEO advice required')
    allowed = {(row.get('evidence_file'), row.get('content_hash')): row
               for row in item.get('valid_evidence', [])}
    refs = raw.get('evidence_refs', [])
    if not isinstance(refs, list) or not refs:
        raise ValueError('Keyword brief requires reviewed evidence references')
    names, sites, evidence = [], set(), []
    config = load(root / 'monitor/config.json', {'sources': []})
    brands = {source['id']: source.get('brand_name') or re.sub(r'\.(?:net|com|co|video|wiki)$', '', source['name'])
              for source in config['sources']}
    quote = raw.get('name_quote')
    named = False
    for ref in refs:
        key = (ref.get('evidence_file'), ref.get('content_hash'))
        row = allowed.get(key)
        if not row:
            raise ValueError('Brief evidence is not bound to the reviewed opportunity')
        path = (root / key[0]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Brief evidence outside root')
        record = load(path, {})
        if not verified(record, key[1]):
            raise ValueError('Brief evidence is no longer verified')
        if isinstance(quote, str) and quote and main.casefold() in quote.casefold() and (
                quote in record.get('title', '') or quote in record.get('text', '')):
            named = True
        brand = brands.get(row.get('source_id'))
        if not brand:
            raise ValueError('Reviewed evidence needs a configured platform brand')
        names.append(public_text(brand, 'platform', 100))
        sites.add(website(record.get('url', row['url'])))
        evidence.append({'evidence_file': key[0], 'content_hash': key[1]})
    if not named:
        raise ValueError('Main term needs a verbatim name quote from reviewed evidence')
    eligibility = item['eligibility']
    if eligibility['priority_tier'] == 1 and not eligibility.get('fresh_48h'):
        raise ValueError('New-launch signal requires verified fresh publication')
    if eligibility['priority_tier'] == 2 and len(sites) < 2:
        raise ValueError('Cross-platform signal needs evidence from two real websites')
    result.update(name_quote=quote, evidence_refs=evidence, platforms=sorted(set(names)))
    return result


def submit(root, briefs):
    events = {}
    for path in (root / 'outbox').glob('*.json'):
        for item in load(path, {}).get('items', []):
            events[item['event_id']] = item
    operations = []
    seen = set()
    for raw in briefs:
        identifier = raw.get('event_id')
        if identifier in seen or identifier not in events or not re.fullmatch('[0-9a-f]{64}', identifier or ''):
            raise ValueError('Unknown or duplicate keyword event')
        seen.add(identifier)
        item = events[identifier]
        if item.get('eligibility', {}).get('priority_tier') not in (1, 2):
            raise ValueError('Only P1/P2 events may have publishable briefs')
        result = validate(root, item, raw)
        result['reviewed_at'] = monitor.utc_now()
        operations.append(op('keyword-briefs/' + identifier + '.json', result))
    if operations:
        commit(root, 'keyword-brief-submit', operations)
    return {'saved': len(operations)}


def render(root, plan, blocked_ids=()):
    lines, missing = [], []
    items = selected(root, plan, blocked_ids)
    for item in items:
        identifier = item['event_id']
        raw = load(root / 'keyword-briefs' / (identifier + '.json'), {})
        try:
            brief = validate(root, item, raw)
            if raw.get('item_hash') != digest(item):
                raise ValueError('Brief belongs to a different opportunity version')
        except (ValueError, TypeError, KeyError) as error:
            missing.append({'event_id': identifier, 'reason': str(error)})
            continue
        platforms = '、'.join(brief['platforms'])
        signal = ('新上线信号：' + platforms + '新上线相关模板' if item['eligibility']['priority_tier'] == 1
                  else '跨平台出现：' + platforms + '出现同类玩法')
        lines.append('\n'.join([brief['main_term'],
            '近义/相关词建议：' + '；'.join(brief['related_terms']),
            '长尾词建议：' + '；'.join(brief['long_tail_terms']),
            'SEO建议：' + brief['seo_advice'], '趋势信号：' + signal,
            '来源平台：' + platforms]))
    # The caller retains missing briefs as an internal failure, never as a claim
    # that an incomplete run found no opportunities.
    return '\n\n'.join(lines) if lines else EMPTY, {'selected': len(items), 'missing': missing}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['submit', 'preview', 'candidates'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--execution-id')
    args = parser.parse_args(argv)
    with locked(args.root):
        if args.command == 'submit':
            result = submit(args.root, load(args.input, []))
        else:
            plan = load(args.root / 'review-runs' / (args.execution_id + '.json'), {})
            if args.command == 'candidates':
                result = {'items': selected(args.root, plan)}
            else:
                text, status = render(args.root, plan)
                if status['missing']:
                    raise ValueError('Required keyword briefs incomplete; see internal evidence records')
                result = {'text': text, **status}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
