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


def test_recovered_collection_appends_without_resetting_existing_work(tmp_path):
    recent=datetime.now(timezone.utc).isoformat()
    save(tmp_path/'pending-observations.json',{'observations':[
        {'observation_id':'a','source_id':'a','first_seen':recent}]})
    original=plan.create(tmp_path,'run')
    original['deferred']={'a':'HTTP 403'}
    save(tmp_path/'review-runs/run.json',original)
    save(tmp_path/'pending-observations.json',{'observations':[
        {'observation_id':'b','source_id':'b','first_seen':recent}]})
    save(tmp_path/'latest-scan.json',{'run_id':'new-config-batch','source_count':59})
    result=plan.bind_collection(tmp_path,'run',0)
    assert [r['observation_id'] for r in result['items']]==['a','b']
    assert result['started_at']==original['started_at']
    assert result['deferred']=={'a':'HTTP 403'}
    assert result['collection_run_id']=='new-config-batch' and result['collection_exit']==0


def test_full_pending_plans_same_source_history_and_extends_late_discovery(tmp_path):
    rows=[{'observation_id':str(i),'source_id':'same','first_seen':'2020-01-01T00:00:00Z'} for i in range(40)]
    save(tmp_path/'pending-observations.json',{'observations':rows})
    original=plan.create(tmp_path,'full',full_pending=True,report_window_start='2026-10-20T10:00:00Z')
    assert len(original['items'])==40 and original['review_scope']=='all_pending'
    assert original['report_window_start']=='2026-10-20T10:00:00Z'
    rows.append({'observation_id':'late','source_id':'same','first_seen':'2020-01-01T00:00:00Z'})
    save(tmp_path/'pending-observations.json',{'observations':rows})
    extended=plan.extend_pending(tmp_path,original)
    assert len(extended['items'])==41
    assert len(plan.extend_pending(tmp_path,extended)['items'])==41
    assert extended['started_at']==original['started_at']


def test_collection_snapshot_survives_later_scans(tmp_path):
    save(tmp_path/'pending-observations.json',{'observations':[]})
    original=plan.create(tmp_path,'full',full_pending=True)
    scan={'run_id':'original','status':'ok','scan_phase':'complete','source_count':59}
    save(tmp_path/'latest-scan.json',scan)
    save(tmp_path/'latest-template-scan.json',{'status':'ok','tier':'daily'})
    result=plan.bind_collection(tmp_path,'full',0)
    save(tmp_path/'latest-scan.json',{'run_id':'later'})
    assert result['collection_scan']==scan
    assert result['supplemental_scans']['latest-template-scan.json']['tier']=='daily'


def test_bind_uses_actual_collector_snapshots_even_if_latest_already_changed(tmp_path):
    save(tmp_path/'pending-observations.json',{'observations':[]})
    plan.create(tmp_path,'full',full_pending=True)
    save(tmp_path/'latest-scan.json',{'run_id':'other-execution'})
    save(tmp_path/'latest-template-scan.json',{'status':'ok','tier':'core'})
    snapshots={'scan':{'run_id':'this-execution','source_count':59},
               'latest-template-scan.json':{'status':'ok','tier':'weekly'},
               'latest-pippit-template-scan.json':{'status':'ok'}}
    result=plan.bind_collection(tmp_path,'full',0,snapshots=snapshots)
    assert result['collection_run_id']=='this-execution'
    assert result['supplemental_scans']['latest-template-scan.json']['tier']=='weekly'
