"""Business publication history comes from complete, verified daily deliveries."""
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .eligibility import instant
from .path_evidence import policy
from .queue_store import canonical_path, digest, load


def enabled(root, plan):
    return bool(policy(root, plan['started_at']) and load(root / 'monitor/config.json', {})
                .get('review_workflow', {}).get('include_first_cross_platform'))


def manual_deliveries(root, plan, same_day=False):
    setting = load(root / 'monitor/config.json', {}).get('review_workflow', {})
    directory = setting.get('manual_delivery_receipts_dir')
    if not directory:
        return []
    day = instant(plan['started_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
    result = []
    for path in Path(directory).glob('*.json'):
        record = load(path, {})
        text = record.get('text')
        base, mail = record.get('base_receipt', {}), record.get('mail_receipt', {})
        if (record.get('report_kind') != 'manual_trial' or record.get('complete') is not True
                or not isinstance(text, str) or not text or not record.get('created_at')
                or base.get('status') != 'confirmed_base' or mail.get('status') != 'verified_sent'
                or record.get('text_hash') != digest(text)
                or base.get('text_hash') != digest(text) or mail.get('text_hash') != digest(text)):
            continue
        try:
            created = instant(record['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
            reported = date.fromisoformat(record.get('report_date', ''))
        except (ValueError, TypeError):
            continue
        in_window = (created == day and instant(record['created_at']) < instant(plan['started_at'])
                     if same_day else created < day)
        if reported == created and in_window:
            result.append(record)
    return result


def manual_subjects(root, plan, same_day=False):
    subjects = set()
    for record in manual_deliveries(root, plan, same_day):
        for item in record.get('published_items', []):
            for row in item.get('source_urls', []):
                source, url = row.get('source_id'), row.get('url')
                if (isinstance(source, str) and re.fullmatch(r'[a-zA-Z0-9_-]+', source)
                        and isinstance(url, str) and urlsplit(url).scheme in {'http', 'https'}
                        and urlsplit(url).hostname):
                    subjects.add((source, canonical_path(url, {'id': source})))
    return subjects


def published(root, plan, same_day=False):
    seen = set() if same_day else set(plan.get('previously_reported_need_ids', []))
    setting = load(root / 'monitor/config.json', {}).get('review_workflow', {})
    directory = setting.get('delivery_receipts_dir')
    if not directory:
        raise ValueError('First cross-platform reporting requires its existing daily delivery directory')
    day = instant(plan['started_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
    texts = []
    for path in Path(directory).glob('*.json'):
        record = load(path, {})
        text = record.get('text', '')
        base, mail = record.get('base_receipt', {}), record.get('mail_receipt', {})
        if (not record.get('complete') or not record.get('created_at') or not text
                or base.get('status') != 'confirmed_base' or mail.get('status') != 'verified_sent'
                or base.get('text_hash') != digest(text) or mail.get('text_hash') != digest(text)):
            continue
        created = instant(record['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).date()
        if same_day:
            if created != day or instant(record['created_at']) >= instant(plan['started_at']):
                continue
        elif created >= day:
            continue  # Preserve same-day rerun windows and the current frozen report.
        execution = record.get('execution_id', '')
        if execution and Path(execution).name == execution:
            prior = load(root / 'review-runs' / (execution + '.json'), {})
            gate = load(root / 'review-runs' / (execution + '.keyword-report.json'), {})
            seen.update(prior.get('previously_reported_need_ids', []))
            seen.update(gate.get('keyword_briefs', {}).get('included_need_ids', []))
        texts.append(text)
    # Existing immutable daily records predate structured included-need IDs.
    # Bind their exact word headings to the existing evidence-bound brief IDs.
    for path in (root / 'keyword-briefs').glob('*.json'):
        brief = load(path, {})
        name = brief.get('main_term')
        escaped = (name or '').replace('\\', '\\\\').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')
        exact = any(re.search(r'(?m)^' + re.escape(name or '') + r'\s*$', text)
                    or re.search(r'(?m)^\|\s*' + re.escape(escaped) + r'\s*\|', text) for text in texts)
        legacy = any(re.search(r'(?<!\w)' + re.escape(name or '') + r'(?!\w)', line, re.I)
                     for text in texts for line in text.splitlines()
                     if line.startswith('原始线索名称/标题：'))
        if name and brief.get('need_id') and (exact or legacy):
            seen.add(brief['need_id'])
    for record in manual_deliveries(root, plan, same_day):
        seen.update(key for key in record.get('published_need_ids', [])
                    if isinstance(key, str) and re.fullmatch(r'[a-zA-Z0-9_-]+', key))
    subjects = manual_subjects(root, plan, same_day)
    if subjects:
        for need in load(root / 'opportunity-registry.json', {}).get('opportunities', []):
            if any((row.get('source_id'), canonical_path(row['url'], {'id': row.get('source_id')}))
                   in subjects for row in need.get('valid_evidence', []) if row.get('url')):
                seen.add(need['id'])
    return seen
