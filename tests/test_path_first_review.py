import copy
import pytest

from radar.ai_monitor import daily_scan, path_evidence, review_pages, review_queue, templates
from radar.ai_monitor.evidence_metadata import verified as page_verified
from radar.ai_monitor.keyword_brief import validate as validate_brief
from radar.ai_monitor.queue_store import digest, load, monitor, write
from radar.ai_monitor.report_signal import validate as validate_signal

AT = '2026-10-22T00:01:00+00:00'
SOURCE = {'id': 'first', 'name': 'First', 'sitemap_urls': ['https://first.com/sitemap.xml']}


@pytest.fixture
def enabled(tmp_path, monkeypatch):
    write(tmp_path / 'monitor/config.json', {'sources': [SOURCE],
          'review_workflow': {'path_first_from': '2026-10-22'}})
    monkeypatch.setattr(monitor, 'utc_now', lambda: AT)
    return tmp_path


@pytest.mark.parametrize('path, expected', [
    ('/ai-video/hotel-lobby-ai-video-generator', 'hotel lobby ai video generator'),
    ('/effects/bulin47', 'bulin47'),
    ('/create/%ED%87%B4%EC%82%AC%ED%95%A0%EA%B2%8C%EC%9A%94-ai', '퇴사할게요 ai'),
    ('/template-detail/hotel-lobby-1234567890123456789', 'hotel lobby'),
    ('/motion/11111111-1111-1111-1111-111111111111', None),
    ('/template-detail/1234567890123456789', None),
    ('/apps/4f67f4f0ab2d3e4f', None),
    ('/create/ai-video-generator', None),
    ('/', None),
])
def test_readable_names_and_opaque_ids(path, expected):
    assert path_evidence.path_name('https://first.com' + path) == expected


def test_shanghai_activation_does_not_change_today(enabled, monkeypatch):
    monkeypatch.setattr(monitor, 'utc_now', lambda: '2026-10-21T15:59:59+00:00')
    assert path_evidence.create(enabled, 'https://first.com/bulin47', source_id='first') is None
    monkeypatch.setattr(monitor, 'utc_now', lambda: '2026-10-21T16:00:00+00:00')
    value = path_evidence.create(enabled, 'https://first.com/bulin47', source_id='first')
    assert value['path_first_from'] == '2026-10-22'
    assert path_evidence.verified(enabled, value)
    assert not page_verified(value)


def test_readable_review_does_not_fetch_page_and_integrity_is_checked(enabled, monkeypatch):
    url = 'https://first.com/hotel-lobby-ai'
    write(enabled / 'pending-observations.json', {'observations': [
        {'source_id': 'first', 'evidence_urls': [url]}]})
    monkeypatch.setattr(review_pages, 'read_page', lambda *a: pytest.fail('Readable paths must not fetch HTML'))
    value = review_pages.fetch(url, enabled)
    assert value['title'] == 'hotel lobby ai'
    assert value['http_status'] is None and not value['page_content_fetched']
    assert path_evidence.verified(enabled, value, value['content_hash'])
    changed = copy.deepcopy(value)
    changed['title'] = 'Made Up Word'
    assert not path_evidence.verified(enabled, changed)
    changed['metadata_hash'] = digest({k: v for k, v in changed.items() if k != 'metadata_hash'})
    assert not path_evidence.verified(enabled, changed)


def test_unknown_id_still_reads_the_page(enabled, monkeypatch):
    url = 'https://first.com/11111111-1111-1111-1111-111111111111'
    seen = []
    body = 'Bulin47 AI turns a portrait into a dance video. ' * 4
    def read(link, opener):
        seen.append(link)
        return {'url': link, 'title': 'Bulin47 AI', 'text': body, 'content_hash': digest(body),
                'valid': True, 'http_status': 200, 'checked_at': AT}
    monkeypatch.setattr(review_pages, 'read_page', read)
    value = review_pages.fetch(url, enabled)
    assert seen == [url] and page_verified(value)


def test_catalog_already_identifies_opaque_id_without_second_fetch(enabled, monkeypatch):
    url = 'https://first.com/11111111-1111-1111-1111-111111111111'
    write(enabled / 'pending-observations.json', {'observations': [
        {'source_id': 'first', 'evidence_urls': [url], 'template_entity_id': 'first:template',
         'is_first_seen': True, 'template_change_type': 'new_entity', 'observed_at': AT}]})
    write(enabled / 'video-template-candidates.json', {'entities': {'first:template': {
        'entity_id': 'first:template', 'name': 'Bulin47 AI', 'urls': [url], 'fingerprint': 'known'}}})
    monkeypatch.setattr(review_pages, 'read_page', lambda *a: pytest.fail('Already named catalog entity must be reused'))
    value = review_pages.fetch(url, enabled)
    assert value['title'] == 'Bulin47 AI' and value['name_origin'] == 'template_catalog'
    assert path_evidence.verified(enabled, value) and value['supply_added_at'] == AT


def test_real_collector_and_review_contract_publish_name_with_real_addition(enabled):
    old = 'https://first.com/old-dance'
    url = 'https://first.com/hotel-lobby-ai'
    previous, _ = monitor.reconcile(SOURCE, None, {old: {'lastmod': None}}, '2026-10-20T00:00:00Z')
    def xml(link, options):
        return ('<urlset>' + ''.join('<url><loc>' + u + '</loc></url>' for u in (old, url)) + '</urlset>').encode()
    _, result, observations = daily_scan.collect(SOURCE, {}, previous, xml,
        lambda link: pytest.fail('Named URL must skip page fetching'),
        lambda link, facts: path_evidence.collector(enabled, SOURCE, link, facts))
    assert len(observations) == 1 and result['baseline_created'] is False
    observation = observations[0]
    value = load(enabled / observation['evidence'][0]['evidence_file'], {})
    assert value['supply_added_at'] == AT
    write(enabled / 'pending-observations.json', {'observations': observations})
    refs = [{key: value[key] for key in ('url', 'evidence_file', 'content_hash')}]
    outcome = review_queue.submit(enabled, [{'observation_id': observation['observation_id'],
        'action': 'accept', 'category': 'video_template', 'need_id': 'hotel-lobby',
        'need': 'Hotel Lobby AI video template', 'evidence': refs}])
    box = load(enabled / 'outbox' / (outcome['outbox_id'] + '.json'), {})
    item = box['items'][0]
    assert item['eligibility']['observed_new_supply'] and not item['eligibility']['fresh_48h']
    brief = {'event_id': item['event_id'], 'main_term': 'Hotel Lobby AI',
        'related_terms': ['hotel lobby video template'], 'long_tail_terms': ['make a Hotel Lobby AI video'],
        'seo_advice': 'Hotel Lobby AI 建具名模板制作页，覆盖相关词与制作步骤，同一制作意图在同页展示案例。',
        'name_quote': value['title'], 'evidence_refs': refs}
    assert validate_brief(enabled, item, brief)['main_term'] == 'Hotel Lobby AI'
    receipt_path = 'batch-runs/2026-10-22-run/sources/first/0001-receipt.json'
    receipt = {'source_id': 'first', 'status': 'committed', 'result': result}
    write(enabled / receipt_path, receipt)
    plan = {'execution_id': 'tomorrow', 'started_at': '2026-10-22T00:00:00Z',
            'report_window_start': '2026-10-22T00:00:00Z', 'report_window_end': '2026-10-23T00:00:00Z'}
    signal = {'execution_id': 'tomorrow', 'event_id': item['event_id'], 'item_hash': digest(item),
        'window_start': plan['report_window_start'], 'window_end': plan['report_window_end'],
        'decision': 'include', 'reason': '正常既有基线中新出现了具名模板路径，按路径识别词；没有把新增路径误称为需求首次发布。',
        'evidence_refs': refs, 'signal_kind': 'new_supply', 'signal_at': AT,
        'signal_quote': value['summary'], 'signal_text': 'First 新增了 Hotel Lobby AI 具名模板页面。',
        'supply_receipt': {'path': receipt_path, 'sha256': digest(receipt), 'source_id': 'first', 'url': url}}
    assert validate_signal(enabled, plan, item, signal)['decision'] == 'include'
    signal['signal_kind'] = 'publication'
    with pytest.raises(ValueError, match='only support'):
        validate_signal(enabled, plan, item, signal)


def test_initial_inventory_never_claims_a_new_supply_time(enabled):
    url = 'https://first.com/hotel-lobby-ai'
    value = path_evidence.collector(enabled, SOURCE, url, {'observed_at': AT,
        'baseline_created': True, 'added': [{'url': url, 'first_seen': AT}]})
    assert value['supply_added_at'] is None


def test_higgs_readable_detail_skips_html(enabled, monkeypatch):
    monkeypatch.setattr(templates, 'MAPS', {'core': ['motion/sitemap.xml'], 'daily': [], 'weekly': []})
    monkeypatch.setattr(templates, 'AGGREGATES', [])
    urls = ['https://higgsfield.ai/motion/old-dance']
    def xml(link, options):
        return ('<urlset>' + ''.join('<url><loc>' + u + '</loc></url>' for u in urls) + '</urlset>').encode()
    no_page = lambda *a: pytest.fail('Readable Higgs detail must not fetch HTML')
    assert templates.run(enabled, fetcher=xml, page_reader=no_page) == 0
    urls.append('https://higgsfield.ai/motion/hotel-lobby-ai')
    assert templates.run(enabled, fetcher=xml, page_reader=no_page) == 0
    pool = load(enabled / 'video-template-candidates.json', {})
    assert any(row['name'] == 'hotel lobby ai' for row in pool['entities'].values())
