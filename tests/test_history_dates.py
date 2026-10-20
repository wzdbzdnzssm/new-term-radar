import pytest

from radar.ai_monitor.evidence_metadata import compact, prepare_history, history_instant
from radar.ai_monitor.eligibility import ai_history_dates, instant
from radar.ai_monitor.queue_store import digest, write
from radar.ai_monitor.review_queue import validate_history


@pytest.mark.parametrize('token', ['October 15, 2026', '15 October 2026', '2026-10-15', 'october 15, 2026'])
def test_history_date_roundtrip_preserves_quote_hash_and_utc(tmp_path, token):
    quote = 'AI template first appeared ' + token
    record = {'url': 'https://example.com/template', 'evidence_file': 'page-evidence/page.json',
              'valid': True, 'http_status': 200, 'text': quote, 'content_hash': digest(quote)}
    request = {'original': quote, 'date_text': token, 'published_at': '2026-10-15',
               'scope': 'same_ai_video_need'}
    checked = prepare_history(record, [request])
    assert checked[0]['original'] == quote
    assert checked[0]['date_text'] == token
    assert history_instant(token) == instant('2026-10-15T00:00:00Z')
    assert ai_history_dates(record)[0]['published_at'] == '2026-10-15T00:00:00+00:00'
    metadata = compact(record)
    assert token in metadata['date_contexts'][0]['excerpt']
    write(tmp_path / record['evidence_file'], metadata)
    evidence = [{'evidence_file': record['evidence_file'], 'content_hash': record['content_hash'], 'url': record['url']}]
    assert validate_history(tmp_path, evidence, checked)[0]['veto_only'] is True
    wrong_date = [{**checked[0], 'published_at': '2026-10-16'}]
    with pytest.raises(ValueError, match='does not match'):
        validate_history(tmp_path, evidence, wrong_date)
    with pytest.raises(ValueError, match='does not match'):
        prepare_history(record, [{**request, 'published_at': '2026-10-16'}])
    with pytest.raises(ValueError, match='hash-bound'):
        validate_history(tmp_path, evidence, [{**checked[0], 'content_hash': 'tampered'}])
    with pytest.raises(ValueError, match='quote/locator'):
        validate_history(tmp_path, evidence, [{**checked[0], 'original': 'unrelated ' + quote}])


@pytest.mark.parametrize('token', ['October 2026', 'Oct 15, 2026', 'October 15', 'October 32, 2026',
                                  'February 29, 2026', 'sometime October 15, 2026', '10/15/26'])
def test_history_date_rejects_fuzzy_or_invalid_dates(token):
    with pytest.raises(ValueError):
        history_instant(token)


def test_iso_history_timezone_contract_unchanged():
    assert history_instant('2026-10-15T02:00:00+02:00') == instant('2026-10-15T00:00:00Z')
