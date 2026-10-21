import json
from radar.ai_monitor import scheduled_scan


def test_native_execution_recollects_same_day_but_same_execution_resumes(tmp_path,monkeypatch):
    (tmp_path/'monitor').mkdir()
    (tmp_path/'monitor/config.json').write_text(json.dumps({'sources':[{'id':'example','enabled':True}]}))
    calls=[]
    def worker(command,log,inherited):
        receipt=command[command.index('--receipt')+1]
        path=tmp_path/receipt;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'source_id':'example','status':'committed','result':{'id':'example','status':'ok'}}))
        calls.append(receipt);return 0,1
    monkeypatch.setattr(scheduled_scan,'wait_worker',worker)
    first={};second={}
    assert scheduled_scan.run(tmp_path,run_day='2026-10-21',execution_id='first',snapshot=first)==0
    old=(tmp_path/calls[0]).read_bytes()
    assert scheduled_scan.run(tmp_path,run_day='2026-10-21',execution_id='first')==0
    assert scheduled_scan.run(tmp_path,run_day='2026-10-21',execution_id='second',snapshot=second)==0
    assert len(calls)==2 and first['run_id']!=second['run_id']
    assert first['run_id'].endswith('-first') and second['run_id'].endswith('-second')
    assert (tmp_path/calls[0]).read_bytes()==old
