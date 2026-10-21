import copy
import json
from pathlib import Path

import pytest

from radar.ai_monitor.keyword_brief import EMPTY, public_text, render, selected, submit, validate
from radar.ai_monitor.queue_store import digest
from radar.ai_monitor.report_signal import submit as submit_signals, validate as validate_signal
from radar.ai_monitor.report_signal import supply_evidence


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
        record.update(storage_version=1, content_verified=True,
                      publication_dates=[{'published_at':'2026-10-20T08:00:00Z','veto_only':False}])
        record['metadata_hash']=digest(record)
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
    plan={'execution_id':'run','started_at':'2026-10-20T07:00:00Z',
          'report_window_start':'2026-10-20T00:00:00Z','report_window_end':'2026-10-21T00:00:00Z'}
    submit_signals(tmp_path,plan,[_signal(item,plan)])
    return tmp_path, item, brief, plan


def _signal(item,plan,decision='include'):
    return {'execution_id':plan['execution_id'],'event_id':item['event_id'],'item_hash':digest(item),
        'window_start':plan['report_window_start'],'window_end':plan['report_window_end'],
        'decision':decision,'reason':'已逐主体核对原始发布时间和内容，确认此证据属于同一AI玩法，排除导航及价格促销变化。',
        'evidence_refs':[dict((k,item['valid_evidence'][0][k]) for k in ('evidence_file','content_hash'))],
        'signal_kind':'publication','signal_at':'2026-10-20T08:00:00Z',
        'signal_quote':'Named Dance AI Generator','signal_text':'新上线信号：First发布同一照片抛帽玩法'}


def test_bound_brief_renders_only_business_fields_without_mutating_outbox(case):
    root, item, brief, plan = case
    old = (root / 'outbox/box.json').read_bytes()
    submit(root, [brief])
    text, status = render(root, plan)
    assert status == {'selected': 1, 'included':1, 'excluded':[], 'missing': []}
    for value in ('Named Dance AI', '| 新词 | 近义词 | 长尾词 | 信息来源 |', 'First', 'Second'):
        assert value in text
    for value in ('http', '.com', '.net', item['id'], item['event_id'], '扫描', '审核', 'P3', 'SHA', '执行',
                  'SEO建议', '趋势信号', '新上线信号'):
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


def test_report_window_includes_prior_plan_and_midnight_but_excludes_already_covered_and_future(case):
    root,item,brief,plan=case
    plan.update(started_at='2026-10-21T03:00:00Z',report_window_start='2026-10-20T10:00:00Z',
                report_window_end='2026-10-21T04:00:00Z')
    for name,stamp in [('covered','2026-10-20T09:59:00Z'),('evening','2026-10-20T12:00:00Z'),
                       ('banana','2026-10-21T00:30:00Z'),('after-cutoff','2026-10-21T04:00:00Z')]:
        current={**item,'id':name,'event_id':digest(name)}
        (root/('outbox/'+name+'.json')).write_text(json.dumps({'outbox_id':name,'created_at':stamp,'items':[current]}))
    assert {row['id'] for row in selected(root,plan)}=={'evening','banana'}
    plan.update(report_window_start='2026-10-21T04:00:00Z',report_window_end='2026-10-22T00:00:00Z')
    assert {row['id'] for row in selected(root,plan)}=={'after-cutoff'}


@pytest.mark.parametrize('field,value', [
    ('main_term', 'photo-cap-toss-dance'), ('main_term', 'Made Up Trend'),
    ('seo_advice', '请做好SEO优化并快速上线以获取更多流量'),
    ('seo_advice', 'Named Dance AI visit https://example.com to optimize the page'),
    ('related_terms', ['free Named Dance AI']), ('long_tail_terms', ['no watermark dance']),
    ('related_terms', 'invalid'), ('long_tail_terms', ['example.com/dance']),
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


def test_brief_can_omit_seo_and_leave_unsubstantiated_suggestions_empty(case):
    root, item, brief, plan = case
    brief.pop('seo_advice')
    brief['related_terms'] = []
    brief['long_tail_terms'] = []
    submit(root, [brief])
    text, status = render(root, plan)
    assert status['missing'] == []
    assert '| Named Dance AI |  |  | First、Second |' in text
    assert 'SEO' not in text and '趋势信号' not in text


def test_table_escapes_source_named_vertical_bars(case):
    root, item, brief, plan = case
    brief['related_terms'] = ['dance | cap toss']
    submit(root, [brief])
    assert 'dance \\| cap toss' in render(root, plan)[0]


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
    signal=_signal(item,plan);signal.update(signal_kind='new_supply',signal_text='跨平台新增供给：First发布同类玩法，Second已有相关模板')
    submit_signals(root,plan,[signal])
    text, _ = render(root, plan)
    assert '| 新词 | 近义词 | 长尾词 | 信息来源 |' in text and 'First、Second' in text
    assert '跨平台新增供给' not in text
    assert '搜索量' not in text and '新上线信号' not in text


def test_late_review_old_p2_can_be_explicitly_excluded_without_brief(case):
    root,item,brief,plan=case
    signal=_signal(item,plan,'exclude')
    submit_signals(root,plan,[signal])
    original=(root/'outbox/box.json').read_bytes()
    text,status=render(root,plan)
    assert text==EMPTY and status=={'selected':1,'included':0,'excluded':[item['event_id']],'missing':[]}
    assert (root/'outbox/box.json').read_bytes()==original


def test_missing_signal_review_blocks_even_with_complete_brief(case):
    root,item,brief,plan=case
    submit(root,[brief]);(root/'report-signals/run.json').unlink()
    text,status=render(root,plan)
    assert text==EMPTY and len(status['missing'])==1
    assert 'Report signal review' in status['missing'][0]['reason']


@pytest.mark.parametrize('field,value',[
    ('item_hash','wrong'),('execution_id','other'),('window_start','2026-10-19T00:00:00Z'),
    ('window_end','2026-10-22T00:00:00Z'),('signal_at','2026-10-20T09:00:00Z'),
    ('signal_quote','Unrelated subject quote'),('signal_kind','checked_at')])
def test_signal_must_match_actual_evidence_window_and_item(case,field,value):
    root,item,brief,plan=case
    raw=_signal(item,plan);raw[field]=value
    with pytest.raises((ValueError,TypeError)):
        validate_signal(root,plan,item,raw)


def test_valid_same_day_rerun_keeps_original_signal_but_next_day_does_not(case):
    root,item,brief,plan=case
    submit(root,[brief])
    rerun={**plan,'started_at':'2026-10-20T12:00:00Z'}
    assert render(root,rerun)[1]['missing']==[]
    next_day={**plan,'report_window_start':'2026-10-21T00:00:00Z','report_window_end':'2026-10-22T00:00:00Z'}
    assert selected(root,next_day)==[]


def test_internal_p2_without_window_signal_is_not_new_trend(case):
    root,item,brief,plan=case
    item['eligibility'].update(priority_tier=2,fresh_48h=False)
    raw=_signal(item,plan)
    with pytest.raises(ValueError,match='Historical'):
        validate_signal(root,plan,item,raw)
    raw.update(signal_kind='new_supply',signal_at='2026-10-19T08:00:00Z')
    with pytest.raises(ValueError,match='outside'):
        validate_signal(root,plan,item,raw)


@pytest.mark.parametrize('invalid', [None,'old_before','same_quote','wrong_subject'])
def test_change_requires_bound_same_subject_before_after_and_semantic_comparison(case,invalid):
    root,item,brief,plan=case
    path=root/'page-evidence/first.json';before=json.loads(path.read_text())
    before['checked_at']='2026-10-20T07:30:00Z'
    if invalid=='old_before':before['checked_at']='2026-10-19T07:30:00Z'
    if invalid=='wrong_subject':before['url']='https://unrelated.com/different'
    before['metadata_hash']=digest({k:v for k,v in before.items() if k!='metadata_hash'})
    (root/'page-evidence/before.json').write_text(json.dumps(before))
    after=copy.deepcopy(before);after.update(title='Named Dance AI Duo Generator',text='Named Dance AI now takes two photos and performs a new paired dance.',checked_at='2026-10-20T08:00:00Z',url='https://first.com/dance')
    after['content_hash']=digest(after['text']);after['metadata_hash']=digest({k:v for k,v in after.items() if k!='metadata_hash'})
    path.write_text(json.dumps(after));item['valid_evidence'][0]['content_hash']=after['content_hash']
    raw=_signal(item,plan);raw.update(signal_kind='content_change',signal_quote=after['title'],
        before_quote=before['title'],before_evidence={'evidence_file':'page-evidence/before.json','content_hash':before['content_hash']},
        substantive_change='已逐条比较主体玩法，从原单张人物照片抛帽舞变为两张人物照片双人配合舞蹈，变化属于实际输入与输出动作。')
    if invalid=='same_quote':raw['signal_quote']=raw['before_quote']='Named Dance AI'
    if invalid:
        with pytest.raises(ValueError):validate_signal(root,plan,item,raw)
    else:
        assert validate_signal(root,plan,item,raw)['decision']=='include'


@pytest.mark.parametrize('invalid',[None,'inventory','scope_reset','restored','wrong_url','wrong_hash'])
def test_new_supply_accepts_real_baseline_addition_without_detail_publication(case,invalid):
    root,item,brief,plan=case
    item['eligibility'].update(priority_tier=2,fresh_48h=False)
    value={'status':'committed','source_id':'first','result':{'status':'ok','baseline_created':False,
        'scope_reset':False,'observed_at':'2026-10-20T08:00:00Z',
        'added':[{'url':'https://first.com/dance','first_seen':'2026-10-20T08:00:00Z'}]}}
    if invalid=='inventory':value['result']['baseline_created']=True
    if invalid=='scope_reset':value['result']['scope_reset']=True
    if invalid=='restored':value['result']['added'][0]['first_seen']='2026-10-14T08:00:00Z'
    relative='batch-runs/2026-10-20-batch/sources/first/1-receipt.json'
    path=root/relative;path.parent.mkdir(parents=True);path.write_text(json.dumps(value))
    raw=_signal(item,plan);raw.update(signal_kind='new_supply',signal_text='First已建立基线的模板索引新增此玩法，Second已有相关模板',
        supply_receipt={'path':relative,'sha256':digest(value),'source_id':'first','url':'https://first.com/dance'})
    if invalid=='wrong_url':raw['supply_receipt']['url']='https://first.com/different'
    if invalid=='wrong_hash':raw['supply_receipt']['sha256']='wrong'
    if invalid:
        with pytest.raises(ValueError):validate_signal(root,plan,item,raw)
    else:
        assert validate_signal(root,plan,item,raw)['signal_kind']=='new_supply'


def test_supply_query_keeps_shanghai_after_midnight_directory_on_previous_utc_day(case):
    root,item,brief,plan=case
    plan.update(report_window_start='2026-10-20T15:00:00Z',report_window_end='2026-10-20T18:00:00Z')
    value={'status':'committed','source_id':'first','result':{'status':'ok','baseline_created':False,
        'scope_reset':False,'observed_at':'2026-10-20T17:00:00Z',
        'added':[{'url':'https://first.com/dance','first_seen':'2026-10-20T17:00:00Z'}]}}
    path=root/'batch-runs/2026-10-21-batch/sources/first/1-receipt.json'
    path.parent.mkdir(parents=True);path.write_text(json.dumps(value))
    assert supply_evidence(root,plan,[item])[0]['url']=='https://first.com/dance'


def test_supply_query_preserves_all_sources_with_legacy_missing_source_rows(case):
    root,item,brief,plan=case
    before=copy.deepcopy(item)
    expected=[]
    for source in ('first','second'):
        url='https://'+source+'.com/dance'
        relative='batch-runs/2026-10-20-batch/sources/'+source+'/1-receipt.json'
        value={'status':'committed','source_id':source,'result':{'status':'ok',
            'baseline_created':False,'observed_at':'2026-10-20T08:00:00Z',
            'added':[{'url':url,'first_seen':'2026-10-20T08:00:00Z'}]}}
        path=root/relative;path.parent.mkdir(parents=True);path.write_text(json.dumps(value))
        expected.append((source,url))
    item['valid_evidence'].extend([{'url':'https://legacy.com/dance'},
        {'source_id':None,'url':'https://legacy.com/other'}])
    original=copy.deepcopy(item)
    actual=supply_evidence(root,plan,[item])
    assert sorted((row['source_id'],row['url']) for row in actual)==expected
    assert item==original and item['valid_evidence'][:len(before['valid_evidence'])]==before['valid_evidence']


@pytest.mark.parametrize('hold,missing', [('report_signal_review_required',False),
                                        ('another_hold',False),('report_signal_review_required',True)])
def test_real_signal_verify_releases_only_quality_hold_after_all_reviews(case,monkeypatch,hold,missing):
    from radar.ai_monitor.report_signal import main
    root,item,brief,plan=case
    submit_signals(root,plan,[_signal(item,plan,'exclude')])
    if missing:(root/'report-signals/run.json').unlink()
    plan['delivery_hold']=hold
    path=root/'review-runs/run.json';path.parent.mkdir();path.write_text(json.dumps(plan))
    monkeypatch.setattr('sys.argv',['report_signal','verify','--root',str(root),'--execution-id','run'])
    if missing:
        with pytest.raises(ValueError):main()
    else:main()
    after=json.loads(path.read_text())
    assert after.get('delivery_hold') == (None if hold=='report_signal_review_required' and not missing else hold)
