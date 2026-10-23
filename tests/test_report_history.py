import pytest

from radar.ai_monitor import report_history
from radar.ai_monitor.queue_store import digest, write


@pytest.mark.parametrize('complete,publishable,base_status,mail_status,bad_hash,expected', [
    (False, True, 'confirmed_base', 'verified_sent', False, True),
    (True, False, 'confirmed_base', 'verified_sent', False, True),
    (False, False, 'confirmed_base', 'verified_sent', False, False),
    (False, 'true', 'confirmed_base', 'verified_sent', False, False),
    (False, True, 'pending', 'verified_sent', False, False),
    (False, True, 'confirmed_base', 'pending', False, False),
    (False, True, 'confirmed_base', 'verified_sent', True, False),
])
def test_partial_delivery_history_requires_both_verified_channels(
        tmp_path, complete, publishable, base_status, mail_status, bad_hash, expected):
    receipts = tmp_path / 'receipts'
    write(tmp_path / 'monitor/config.json', {'review_workflow': {'delivery_receipts_dir': str(receipts)}})
    text = '| New keyword | Other terms | Longtail | Sources |'
    record = {'complete': complete, 'publishable': publishable, 'text': text,
              'created_at': '2026-10-23T08:00:00Z', 'execution_id': 'prior',
              'base_receipt': {'status': base_status, 'text_hash': digest(text)},
              'mail_receipt': {'status': mail_status, 'text_hash': 'wrong' if bad_hash else digest(text)}}
    write(receipts / 'partial.json', record)
    write(tmp_path / 'review-runs/prior.keyword-report.json', {
        'keyword_briefs': {'included_need_ids': ['need-delivered']}})
    plan = {'started_at': '2026-10-24T00:00:00Z'}
    assert ('need-delivered' in report_history.published(tmp_path, plan)) is expected
    # Daily windows remain distinct: prior-day delivery is not a same-day event.
    assert report_history.published(tmp_path, plan, same_day=True) == set()
