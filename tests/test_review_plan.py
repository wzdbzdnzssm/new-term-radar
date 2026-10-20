import json
from datetime import datetime, timezone, timedelta

from radar.ai_monitor import review_plan as plan


def save(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


def test_fair_plan_rotates_blocked_and_alternates_old_new(tmp_path):
    rows = [{'observation_id': str(i), 'source_id': source, 'first_seen': '2026-10-%02dT00:00:00Z' % (i+1)}
        for i, source in enumerate(['a','a','a','a','b'])]
    save(tmp_path/'pending-observations.json', {'observations': rows})
    first = plan.create(tmp_path, 'z-first')
    assert [r['observation_id'] for r in first['items']] == ['0','4']
    assert plan.create(tmp_path, 'z-first') == first
    second = plan.create(tmp_path, 'a-second')
    assert [r['observation_id'] for r in second['items']] == ['3','4']
    third = plan.create(tmp_path, 'm-third')
    assert third['items'][0]['observation_id'] == '1'
    # Random-looking execution identifiers must not control chronology.
    fourth = plan.create(tmp_path, 'b-fourth')
    assert fourth['items'][0]['observation_id'] == '2'
    fifth = plan.create(tmp_path, 'c-fifth')
    assert fifth['items'][0]['observation_id'] == '0'


def test_progress_uses_utc_instants_and_does_not_count_blocked(tmp_path):
    save(tmp_path/'pending-observations.json', {'observations': []})
    save(tmp_path/'opportunity-registry.json', {'observation_pool': [
        {'observation_id':'a','review_status':'watch','reviewed_at':'2026-10-20T09:01:00+08:00'},
        {'observation_id':'b','review_status':'blocked','blocked_reason':'HTTP 403'},
        {'observation_id':'c','review_status':'exclude','reviewed_at':'2026-10-20T00:59:00Z'}]})
    result=plan.progress(tmp_path, {'execution_id':'run','started_at':'2026-10-20T01:00:00Z',
        'items':[{'observation_id':i} for i in ['a','b','c']]})
    assert result['completed'] == 1 and result['remaining'] == 2
    assert result['items'][1]['blocked_reason'] == 'HTTP 403'


def test_all_recent_same_source_are_planned_not_capped(tmp_path):
    recent=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    rows=[{'observation_id':str(i),'source_id':'same','first_seen':recent} for i in range(12)]
    rows += [{'observation_id':'old','source_id':'same','first_seen':'2020-01-01T00:00:00Z'},
        {'observation_id':'other','source_id':'other','first_seen':'2020-01-01T00:00:00Z'}]
    save(tmp_path/'pending-observations.json',{'observations':rows})
    result=plan.create(tmp_path,'recent')
    assert result['recent_discovery_count']==12
    assert len(result['items'])==14


def test_mixed_timezone_oldest_uses_real_instant(tmp_path):
    rows=[{'observation_id':'old','source_id':'a','first_seen':'2020-01-01T08:00:00+08:00'},
          {'observation_id':'later','source_id':'a','first_seen':'2020-01-01T01:00:00Z'}]
    save(tmp_path/'pending-observations.json',{'observations':rows})
    assert plan.create(tmp_path,'one')['items'][0]['observation_id']=='old'
