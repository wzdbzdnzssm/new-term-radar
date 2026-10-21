import json
import pytest
from radar.ai_monitor import review_pages
from radar.ai_monitor.evidence_metadata import compact
from radar.ai_monitor.queue_store import digest


@pytest.fixture
def history(tmp_path):
    (tmp_path/'page-evidence').mkdir()
    url='https://example.com/old-video'
    (tmp_path/'pending-observations.json').write_text(json.dumps({'observations':[
        {'observation_id':'subject','evidence_urls':[url],'aliases':[]}]}))
    body='Historical photo to dance video instructions. '*30
    record=compact({'url':url,'title':'Old Dance','text':body,'summary':body,'checked_at':'2026-10-14T00:00:00Z',
                    'content_hash':digest(body),'valid':True,'http_status':200})
    (tmp_path/'page-evidence/valid.json').write_text(json.dumps(record))
    (tmp_path/'page-evidence/404.json').write_text(json.dumps(compact({'url':url,'valid':False,'error':'HTTP Error 404'})))
    tampered={**record,'title':'tampered'}
    (tmp_path/'page-evidence/tampered.json').write_text(json.dumps(tampered))
    return tmp_path


def test_historical_subject_returns_verified_bound_compact_metadata_only(history):
    result=review_pages.historical_subject(history,'subject')
    assert result['count']==1
    record=result['records'][0]
    assert record['checked_at']=='2026-10-14T00:00:00Z'
    assert record['historical_snapshot'] is True and record['current_availability_verified'] is False
    assert len(record['summary'])==500 and 'text' not in record and 'raw_html' not in record
    assert record['evidence_file']=='page-evidence/valid.json'


def test_historical_query_cli_does_not_fetch_or_submit(history,monkeypatch,capsys):
    monkeypatch.setattr(review_pages,'fetch',lambda *args:pytest.fail('Must not fetch historical query'))
    before=(history/'pending-observations.json').read_bytes()
    assert review_pages.main(['--root',str(history),'--history-for-observation','subject'])==0
    assert json.loads(capsys.readouterr().out)['count']==1
    assert (history/'pending-observations.json').read_bytes()==before


def test_historical_query_cannot_bind_unknown_subject(history):
    with pytest.raises(ValueError,match='Unknown observation'):
        review_pages.historical_subject(history,'other')
