"""Business publication history comes from complete, verified daily deliveries."""
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .eligibility import instant
from .path_evidence import policy
from .queue_store import digest, load


def enabled(root, plan):
    return bool(policy(root, plan['started_at']) and load(root / 'monitor/config.json', {})
                .get('review_workflow', {}).get('include_first_cross_platform'))


def published(root, plan):
    seen = set(plan.get('previously_reported_need_ids', []))
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
        if instant(record['created_at']).astimezone(ZoneInfo('Asia/Shanghai')).date() >= day:
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
        if name and brief.get('need_id') and any(re.search(r'(?m)^' + re.escape(name) + r'\s*$', text) for text in texts):
            seen.add(brief['need_id'])
    return seen
