import copy

import pytest

from radar.ai_monitor import keyword_brief, path_evidence, report_history, report_signal
from radar.ai_monitor import review_queue
from radar.ai_monitor.eligibility import qualify
from radar.ai_monitor.queue_store import digest, monitor, write


@pytest.fixture
def opportunity(tmp_path, monkeypatch):
    at = '2026-10-22T00:00:00+00:00'
    monkeypatch.setattr(monitor, 'utc_now', lambda: at)
    write(tmp_path / 'monitor/config.json', {'sources': [
        {'id': source, 'name': source.title()} for source in ('first', 'second')],
        'review_workflow': {'path_first_from': '2026-10-22', 'include_first_cross_platform': True,
                            'delivery_receipts_dir': str(tmp_path / 'deliveries')}})
    evidence = []
    for source in ('first', 'second'):
        value = path_evidence.create(tmp_path, 'https://' + source + '.com/hotel-lobby-ai', source_id=source)
        evidence.append({key: value[key] for key in ('url', 'source_id', 'evidence_file', 'content_hash')})
    need = {'id': 'hotel-lobby', 'need_id': 'hotel-lobby', 'need': 'Hotel Lobby AI',
            'category': 'video_template', 'first_seen': '2026-10-15T00:00:00Z', 'valid_evidence': evidence}
    eligibility = qualify(tmp_path, need, at)
    assert eligibility['priority_tier'] == 2
    item = dict(need, event_id='a' * 64, event='new', eligibility=eligibility)
    write(tmp_path / 'opportunity-registry.json', {'opportunities': [need]})
    write(tmp_path / 'outbox/old.json', {'outbox_id': 'old', 'created_at': '2026-10-21T03:00:00Z', 'items': [item]})
    brief = {'event_id': item['event_id'], 'need_id': need['id'], 'item_hash': digest(item),
        'main_term': 'Hotel Lobby AI', 'related_terms': ['hotel lobby video template'],
        'long_tail_terms': ['make a Hotel Lobby AI video'],
        'seo_advice': 'Hotel Lobby AI 建具名模板制作页，在同页展示相关主题案例和制作步骤，覆盖同一搜索意图。',
        'name_quote': 'hotel lobby ai', 'evidence_refs': evidence}
    write(tmp_path / ('keyword-briefs/' + item['event_id'] + '.json'), brief)
    plan = {'execution_id': 'tomorrow', 'started_at': at, 'report_window_start': '2026-10-21T06:15:00Z',
            'report_window_end': '2026-10-22T01:00:00Z'}
    signal = {'execution_id': plan['execution_id'], 'event_id': item['event_id'], 'item_hash': digest(item),
        'window_start': plan['report_window_start'], 'window_end': plan['report_window_end'],
        'decision': 'include', 'reason': '两站路径已明确同一具名制作主题，以首次正式纳入机会日报收录，不声称是今天发布或新出现的需求。',
        'evidence_refs': evidence, 'signal_kind': 'first_cross_platform', 'signal_at': at,
        'signal_quote': 'hotel lobby ai', 'signal_text': '已在 First、Second 平台出现，首次纳入机会日报。'}
    return tmp_path, item, brief, plan, signal


def delivery(root, text, stamp, verified=True):
    write(root / 'deliveries/past.json', {'execution_id': 'past', 'created_at': stamp,
        'text': text, 'complete': True, 'base_receipt': {'status': 'confirmed_base', 'text_hash': digest(text)},
        'mail_receipt': {'status': 'verified_sent' if verified else 'queued', 'text_hash': digest(text)}})


def test_historical_two_platform_unreported_word_can_be_first_business_inclusion(opportunity):
    root, item, brief, plan, signal = opportunity
    assert keyword_brief.selected(root, plan) == [item]
    report_signal.submit(root, plan, [signal])
    text, status = keyword_brief.render(root, plan)
    assert status['included'] == 1 and status['included_need_ids'] == ['hotel-lobby']
    assert '首次纳入机会日报' in text and 'http' not in text


def test_real_past_business_delivery_prevents_repeat_but_internal_ack_does_not(opportunity):
    root, item, brief, plan, signal = opportunity
    delivery(root, 'Hotel Lobby AI\n近义/相关词建议：hotel lobby', '2026-10-21T06:14:00Z')
    assert report_history.published(root, plan) == {'hotel-lobby'}
    assert keyword_brief.selected(root, plan) == []
    with pytest.raises(ValueError, match='unreported'):
        report_signal.validate(root, plan, item, signal)


def test_queued_mail_does_not_mark_a_word_formally_published(opportunity):
    root, item, brief, plan, signal = opportunity
    delivery(root, 'Hotel Lobby AI\n近义/相关词建议：hotel lobby', '2026-10-21T06:14:00Z', verified=False)
    assert keyword_brief.selected(root, plan) == [item]


@pytest.mark.parametrize('prefix,reported', [('原始线索名称/标题：', True), ('原始名称/标题：', False)])
def test_legacy_formal_title_counts_but_an_observation_title_does_not(opportunity, prefix, reported):
    root, item, brief, plan, signal = opportunity
    delivery(root, prefix + 'Hotel Lobby AI Video Generator: Example', '2026-10-21T06:14:00Z')
    assert ('hotel-lobby' in report_history.published(root, plan)) is reported


def test_same_day_freeze_readback_and_rerun_stay_reproducible(opportunity):
    root, item, brief, plan, signal = opportunity
    report_signal.submit(root, plan, [signal])
    text, before = keyword_brief.render(root, plan)
    delivery(root, text, '2026-10-22T00:10:00Z')
    assert keyword_brief.render(root, plan) == (text, before)


def test_reported_word_new_third_source_emits_only_one_compact_recurrence(opportunity):
    root, original, brief, plan, signal = opportunity
    delivery(root, 'Hotel Lobby AI\n近义/相关词建议：hotel lobby', '2026-10-21T06:14:00Z')
    config = review_queue.load(root / 'monitor/config.json', {})
    config['sources'].append({'id': 'third', 'name': 'Third'})
    write(root / 'monitor/config.json', config)
    need = dict(original, source_count=2, event_keys=['new'], source_names=['First', 'Second'])
    write(root / 'opportunity-registry.json', {'opportunities': [need], 'observation_pool': []})
    url = 'https://third.com/hotel-lobby-ai'
    value = path_evidence.create(root, url, source_id='third', supply_at=plan['started_at'])
    observation = {'observation_id': digest('third\n/hotel-lobby-ai'), 'source_id': 'third',
        'source_name': 'Third', 'canonical_path': '/hotel-lobby-ai', 'evidence_urls': [url],
        'aliases': [url], 'first_seen': plan['started_at'], 'observed_at': plan['started_at'], 'is_first_seen': True}
    write(root / 'pending-observations.json', {'observations': [observation]})
    ref = {key: value[key] for key in ('url', 'evidence_file', 'content_hash')}
    decision = {'observation_id': observation['observation_id'], 'action': 'accept',
        'category': 'video_template', 'need_id': need['id'], 'need': 'Hotel Lobby AI', 'evidence': [ref]}
    outcome = review_queue.submit(root, [decision])
    box = review_queue.load(root / 'outbox' / (outcome['outbox_id'] + '.json'), {})
    item = box['items'][0]
    assert item['event'] == 'recurrence'
    assert review_queue.submit(root, [decision])['events'] == 0
    receipt_path = 'batch-runs/2026-10-22-run/sources/third/0001-receipt.json'
    receipt = {'source_id': 'third', 'status': 'committed', 'result': {'status': 'ok',
        'baseline_created': False, 'scope_reset': False, 'observed_at': plan['started_at'],
        'added': [{'url': url, 'first_seen': plan['started_at']}]}}
    write(root / receipt_path, receipt)
    signal.update(event_id=item['event_id'], item_hash=digest(item), signal_kind='recurrence',
        signal_quote=value['summary'], evidence_refs=[ref],
        supply_receipt={'path': receipt_path, 'sha256': digest(receipt), 'source_id': 'third', 'url': url})
    report_signal.submit(root, plan, [signal])
    text, status = keyword_brief.render(root, plan)
    assert status['included'] == 1 and status['missing'] == []
    assert text == 'Hotel Lobby AI：本轮又在Third出现；累计已在3个平台发现（First、Second、Third）。'
    assert not (root / ('keyword-briefs/' + item['event_id'] + '.json')).exists()


def test_unchanged_old_word_cannot_be_called_a_recurrence_without_new_supply(opportunity):
    root, item, brief, plan, signal = opportunity
    delivery(root, 'Hotel Lobby AI\n近义/相关词建议：hotel lobby', '2026-10-21T06:14:00Z')
    signal['signal_kind'] = 'recurrence'
    with pytest.raises(ValueError, match='actual new source'):
        report_signal.validate(root, plan, item, signal)


@pytest.mark.parametrize('fault', ['single_platform', 'same_platform_aliases', 'old_workflow', 'wrong_time'])
def test_cross_platform_claim_needs_two_sources_and_new_explicit_policy(opportunity, fault):
    root, item, brief, plan, signal = opportunity
    signal = copy.deepcopy(signal)
    if fault == 'single_platform':
        signal['evidence_refs'] = signal['evidence_refs'][:1]
    elif fault == 'same_platform_aliases':
        item = copy.deepcopy(item)
        item['valid_evidence'][1]['source_id'] = item['valid_evidence'][0]['source_id']
        signal['item_hash'] = digest(item)
    elif fault == 'old_workflow':
        write(root / 'monitor/config.json', {'sources': []})
    else:
        signal['signal_at'] = '2026-10-21T08:00:00Z'
    with pytest.raises(ValueError):
        report_signal.validate(root, plan, item, signal)
