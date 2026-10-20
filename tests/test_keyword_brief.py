import copy
import json
from pathlib import Path

import pytest

from radar.ai_monitor.keyword_brief import EMPTY, public_text, render, selected, submit, validate
from radar.ai_monitor.queue_store import digest


@pytest.fixture
def case(tmp_path):
    (tmp_path / 'page-evidence').mkdir()
    (tmp_path / 'outbox').mkdir()
    (tmp_path / 'monitor').mkdir()
    (tmp_path / 'monitor/config.json').write_text(json.dumps({'sources': [
        {'id': 'first', 'name': 'First'}, {'id': 'second', 'name': 'Second.net'}]}))
    evidence = []
    for source in ('first', 'second'):
        text = 'Named Dance AI turns one photo into a cap-toss dance.'
        record = {'url': 'https://' + source + '.com/dance', 'title': 'Named Dance AI Generator',
                  'text': text, 'content_hash': digest(text), 'valid': True, 'http_status': 200}
        relative = 'page-evidence/' + source + '.json'
        (tmp_path / relative).write_text(json.dumps(record))
        evidence.append({'source_id': source, 'url': record['url'], 'evidence_file': relative,
                         'content_hash': record['content_hash']})
    item = {'id': 'photo-cap-toss-dance', 'event_id': 'a' * 64, 'event': 'new',
            'eligibility': {'eligible': True, 'priority_tier': 1, 'fresh_48h': True},
            'valid_evidence': evidence}
    box = {'outbox_id': 'box', 'created_at': '2026-10-20T08:00:00Z', 'items': [item], 'text': 'old URL audit'}
    (tmp_path / 'outbox/box.json').write_text(json.dumps(box))
    brief = {'event_id': item['event_id'], 'main_term': 'Named Dance AI',
             'related_terms': ['cap toss AI dance'], 'long_tail_terms': ['Named Dance AI from a photo'],
             'seo_advice': 'Named Dance AI用单个照片转舞蹈模板页，展示照片输入与抛帽动作前后案例，同意图变体在同页自然覆盖。',
             'name_quote': 'Named Dance AI Generator',
             'evidence_refs': [{k: row[k] for k in ('evidence_file', 'content_hash')} for row in evidence]}
    return tmp_path, item, brief, {'started_at': '2026-10-20T07:00:00Z'}


def test_bound_brief_renders_only_business_fields_without_mutating_outbox(case):
    root, item, brief, plan = case
    old = (root / 'outbox/box.json').read_bytes()
    submit(root, [brief])
    text, status = render(root, plan)
    assert status == {'selected': 1, 'missing': []}
    for value in ('Named Dance AI', '近义/相关词建议', '长尾词建议', 'SEO建议', '新上线信号', 'First', 'Second'):
        assert value in text
    for value in ('http', '.com', '.net', item['id'], item['event_id'], '扫描', '审核', 'P3', 'SHA', '执行'):
        assert value not in text
    assert (root / 'outbox/box.json').read_bytes() == old


def test_dedupe_all_p1_no_cap_and_exclude_p3_and_old_events(case):
    root, item, brief, plan = case
    items = []
    for index in range(12):
        current = {**item, 'id': 'need-' + str(index), 'event_id': digest(str(index))}
        items.extend([current, copy.deepcopy(current)])
    items.append({**item, 'id': 'watch', 'eligibility': {'eligible': True, 'priority_tier': 3}})
    items.append({**item, 'id': 'not-new', 'event': 'old'})
    (root / 'outbox/box.json').write_text(json.dumps({'outbox_id': 'box', 'created_at': '2026-10-20T08:00:00Z', 'items': items}))
    assert len(selected(root, plan)) == 12
    assert selected(root, plan, ['box']) == []


def test_missing_brief_is_internal_gap_not_silent_success(case):
    root, item, brief, plan = case
    text, status = render(root, plan)
    assert text == EMPTY
    assert len(status['missing']) == status['selected'] == 1
    assert status['missing'][0]['event_id'] not in text


@pytest.mark.parametrize('field,value', [
    ('main_term', 'photo-cap-toss-dance'), ('main_term', 'Made Up Trend'),
    ('seo_advice', '请做好SEO优化并快速上线以获取更多流量'),
    ('seo_advice', 'Named Dance AI visit https://example.com to optimize the page'),
    ('related_terms', ['free Named Dance AI']), ('long_tail_terms', ['no watermark dance']),
    ('related_terms', []), ('long_tail_terms', ['example.com/dance']),
    ('seo_advice', 'Named Dance AI扫描失败，队列392条待处理，之后再做页面。'),
    ('seo_advice', 'Named Dance AI运行失败，请检查运行状态并恢复该任务。'),
])
def test_rejects_empty_unbound_generic_or_unsupported_content(case, field, value):
    root, item, brief, _ = case
    brief[field] = value
    with pytest.raises(ValueError):
        validate(root, item, brief)


def test_scan_effect_name_is_not_an_operations_label():
    assert public_text('X-ray扫描AI', 'main_term') == 'X-ray扫描AI'


def test_rejects_wrong_hash_and_p2_single_platform(case):
    root, item, brief, _ = case
    bad = copy.deepcopy(brief)
    bad['evidence_refs'][0]['content_hash'] = 'bad'
    with pytest.raises(ValueError):
        validate(root, item, bad)
    item['eligibility'].update(priority_tier=2, fresh_48h=False)
    brief['evidence_refs'] = brief['evidence_refs'][:1]
    with pytest.raises(ValueError):
        validate(root, item, brief)


def test_stale_sidecar_does_not_silently_publish_new_version(case):
    root, item, brief, plan = case
    submit(root, [brief])
    path = root / 'outbox/box.json'
    box = json.loads(path.read_text())
    box['items'][0]['need'] = 'changed'
    path.write_text(json.dumps(box))
    text, status = render(root, plan)
    assert text == EMPTY and status['missing']


def test_p2_only_claims_observed_platform_coverage(case):
    root, item, brief, plan = case
    item['eligibility'].update(priority_tier=2, fresh_48h=False)
    path = root / 'outbox/box.json'
    box = json.loads(path.read_text()); box['items'] = [item]; path.write_text(json.dumps(box))
    submit(root, [brief])
    text, _ = render(root, plan)
    assert '跨平台出现：First、Second出现同类玩法' in text
    assert '搜索量' not in text and '新上线信号' not in text
