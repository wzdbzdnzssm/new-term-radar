from contextlib import nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from radar.ai_monitor import pippit, queue_store as store, daily_scan, core, daily, review_queue


@pytest.fixture
def cards():
    # Compact research samples preserve source strings/IDs; video URLs here are
    # synthetic signed fixtures. No media is requested or stored by these tests.
    rows = json.loads((Path(__file__).parent / 'fixtures/pippit-video-cards.json').read_text(encoding='utf-8'))
    for row in rows:
        row['video_url'] = 'https://cdn.example/video/' + row['web_id'] + '.mp4?signature=one'
    return rows


def html(videos, image=None):
    templates = {'video': videos, 'image': image or []}
    data = {'loaderData': {'templates_(keyword)/page': {'project': {'kep': {'templates': templates,
             'configTemplates': {'video': [{'web_id': '9999999999999999999'}]}}}}}}
    return '<h1>AI video templates</h1><script id="__MODERN_ROUTER_DATA__" type="application/json">' + json.dumps(data) + '</script>'


@pytest.fixture
def radar(tmp_path, monkeypatch):
    if os.name == 'nt':
        # POSIX batch-lock concurrency is covered on Linux; keep the actual queue
        # lock and transactions for Windows fixture/data-flow tests.
        monkeypatch.setattr(pippit, 'batch_lock', lambda root: nullcontext())
    store.write(tmp_path / 'monitor/config.json', {'sources': [deepcopy(pippit.ARTICLE_SOURCE)]})
    return tmp_path


def reader(videos):
    return lambda url: {'url': url, 'valid': True, 'raw_html': html(videos)}


def test_exact_ssr_video_path_preserves_large_ids_names_and_no_images(cards):
    result = pippit.parse_cards(html(cards, image=[{'web_id': '1111111111111111111', 'short_title': 'Static product photo'}]))
    ids = {row['web_id'] for row in result}
    assert '7690864145436101894' in ids and '7688626211769355527' in ids
    hotel = next(row for row in result if row['web_id'] == '7690864145436101894')
    assert hotel['name'] == 'HOTEL LOBBY' and hotel['named_status'] == 'named_candidate'
    assert hotel['url'] == 'https://www.pippit.ai/template-detail/hotel-lobby-7690864145436101894'
    assert 'signature' not in str(result)
    assert '1111111111111111111' not in ids and '9999999999999999999' not in ids


def test_first_inventory_cross_page_dedup_and_repeat_preserve_history(radar, cards):
    old = {'schema_version': 1, 'entities': {'higgsfield-ai:existing': {'name': 'Existing', 'first_seen_at': '2026-01-01'}}}
    store.write(radar / 'video-template-candidates.json', old)
    store.write(radar / 'pending-observations.json', {'observations': []})
    pending_bytes = (radar / 'pending-observations.json').read_bytes()
    assert pippit.run(radar, reader(cards)) == 0
    report = store.load(radar / 'latest-pippit-template-scan.json', {})
    assert report['events'] == [] and report['queued_observation_count'] == 0
    assert not list((radar/'supply-deltas').glob('*.json'))
    pool = store.load(radar / 'video-template-candidates.json', {})['entities']
    assert len(pool) == len(cards) + 1
    assert pool['higgsfield-ai:existing'] == old['entities']['higgsfield-ai:existing']
    assert pool['pippit-ai:7690864145436101894']['change_type'] == 'historical_inventory'
    assert pool['pippit-ai:7690864145436101894']['upload_date_is_demand_origin'] is False
    assert pool['pippit-ai:7690864145436101894']['platform_metrics_are_independent_demand'] is False
    first_seen = pool['pippit-ai:7690864145436101894']['first_seen_at']
    assert pippit.run(radar, reader(cards)) == 0
    assert store.load(radar / 'latest-pippit-template-scan.json', {})['events'] == []
    assert store.load(radar / 'video-template-candidates.json', {})['entities']['pippit-ai:7690864145436101894']['first_seen_at'] == first_seen
    assert (radar / 'pending-observations.json').read_bytes() == pending_bytes


def test_one_new_card_joins_original_queue_export_and_reappearance_is_silent(radar, cards, capsys):
    assert pippit.run(radar, reader(cards[:1])) == 0
    assert pippit.run(radar, reader(cards[:2])) == 0
    report = store.load(radar / 'latest-pippit-template-scan.json', {})
    assert len(report['events']) == 1 and report['queued_observation_count'] == 1
    references=[row['supply_delta'] for row in report['pages'] if 'supply_delta' in row]
    assert len(references)==1
    delta=store.load(radar/references[0]['path'],{})
    assert store.digest(delta)==references[0]['sha256'] and delta['source_id']=='pippit-ai'
    assert delta['baseline_created'] is False and delta['before_observed_at']
    assert len(delta['added_urls'])==1 and cards[1]['web_id'] in delta['added_urls'][0]
    pending = store.load(radar / 'pending-observations.json', {})['observations']
    assert len(pending) == 1
    identifier = cards[1]['web_id']
    assert pending[0]['canonical_path'] == '/template-detail/' + identifier
    assert pending[0]['observation_id'] == store.observation_id('pippit-ai', '/template-detail/' + identifier)
    assert pending[0]['review_status'] == 'pending' and pending[0]['evidence'] == []
    capsys.readouterr()
    assert review_queue.main(['--root', str(radar), 'export']) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported['pippit_template_coverage']['source_id'] == 'pippit-ai'
    assert exported['observations'][0]['template_entity_id'] in exported['template_candidates']
    # The entity disappears from all three lists, then returns: it is history.
    assert pippit.run(radar, reader(cards[:1])) == 0
    assert pippit.run(radar, reader(cards[:2])) == 0
    assert store.load(radar / 'latest-pippit-template-scan.json', {})['events'] == []
    assert len(store.load(radar / 'pending-observations.json', {})['observations']) == 1
    assert len(list((radar/'supply-deltas').glob('*.json')))==1
    assert all('supply_delta' not in row for row in store.load(radar/'latest-pippit-template-scan.json',{})['pages'])


def test_signature_metrics_and_slug_changes_do_not_create_another_entity(radar, cards):
    assert pippit.run(radar, reader(cards)) == 0
    previous = store.load(radar / 'video-template-candidates.json', {})['entities']
    changed = deepcopy(cards)
    for card in changed:
        card['video_url'] = card['video_url'].replace('signature=one', 'signature=two&expires=999')
        card['play_amount'] += 1000
        card['short_title_en'] += '-renamed'
    assert pippit.run(radar, reader(changed)) == 0
    current = store.load(radar / 'video-template-candidates.json', {})['entities']
    assert len(current) == len(previous)
    for key in previous:
        assert current[key]['fingerprint'] == previous[key]['fingerprint']
        assert current[key]['first_seen_at'] == previous[key]['first_seen_at']
        assert len(current[key]['urls']) == 2
    assert store.load(radar / 'latest-pippit-template-scan.json', {})['events'] == []
    assert not list((radar/'supply-deltas').glob('*.json'))
    url = changed[0]['short_title_en'] + '-' + changed[0]['web_id']
    assert store.canonical_path('https://www.pippit.ai/template-detail/' + url, {'id': 'pippit-ai'}) == '/template-detail/' + changed[0]['web_id']


@pytest.mark.parametrize('kind', ['missing_script', 'bad_json', 'empty_video', 'image_only', 'numeric_id'])
def test_invalid_or_empty_ssr_preserves_snapshots_and_reports_partial(radar, cards, kind):
    assert pippit.run(radar, reader(cards)) == 0
    before = {name: (radar / name).read_bytes() for name in ['pippit-template-state.json', 'video-template-candidates.json']}
    if kind == 'missing_script':
        value = '<h1>Client rendered</h1>'
    elif kind == 'bad_json':
        value = '<script id="__MODERN_ROUTER_DATA__">invalid</script>'
    elif kind in ('empty_video', 'image_only'):
        value = html([], cards if kind == 'image_only' else [])
    else:
        malformed = deepcopy(cards)
        malformed[0]['web_id'] = int(malformed[0]['web_id'])
        value = html(malformed)
    assert pippit.run(radar, lambda url: {'valid': True, 'raw_html': value}) == 1
    for name, raw in before.items():
        assert (radar / name).read_bytes() == raw
    report = store.load(radar / 'latest-pippit-template-scan.json', {})
    assert report['status'] == 'partial' and all(row['status'] == 'unread' for row in report['pages'])


def test_generic_names_stay_unverified_not_named_discovery(radar, cards):
    assert pippit.run(radar, reader(cards[:1])) == 0
    generic = deepcopy(cards[0])
    generic.update(web_id='7999999999999999999', short_title='New Trend', short_title_en='new-trend')
    assert pippit.run(radar, reader(cards[:1] + [generic])) == 0
    assert store.load(radar / 'latest-pippit-template-scan.json', {})['events'] == []
    entity = store.load(radar / 'video-template-candidates.json', {})['entities']['pippit-ai:7999999999999999999']
    assert entity['named_status'] == 'generic_or_unclear'
    assert entity['handoff']['category'] == 'generic_video_edit'
    assert entity['handoff']['independent_demand'] == 'unverified'


@pytest.mark.parametrize('name', ['NEW edit', 'Trending New Edit', 'TREND VAIRAL VIDEO', 'TRENDING TOP EDIT'])
def test_real_generic_source_titles_are_not_named_candidates(name, cards):
    card = deepcopy(cards[0])
    card['short_title'] = name
    assert pippit.parse_cards(html([card]))[0]['named_status'] == 'generic_or_unclear'
    assert pippit.generic_name('HOTEL LOBBY') is False
    assert pippit.generic_name('Bulin47') is False


def test_article_scope_and_non_video_intent_are_not_main_video_templates():
    source = pippit.ARTICLE_SOURCE
    assert source['max_sitemaps'] == 1
    assert source['sitemap_urls'] == ['https://www.pippit.ai/sitemap/new/573081/default/index0.xml']
    assert daily_scan.review_lane(source, [{'valid': True, 'title': 'Hotel Lobby', 'summary': 'Create this dance video from your photo'}], []) == 'video_candidate'
    assert daily_scan.review_lane(source, [{'valid': True, 'title': 'Advertising budget', 'summary': 'Grow your business'}], []) == 'supplemental_evidence'
    assert daily_scan.review_lane(source, [{'valid': True, 'title': 'Holi poster', 'summary': 'Navigation: try our video generator'}], []) == 'static_image'


def test_core_keeps_higgs_and_pippit_failures_visible(radar, monkeypatch):
    calls = []
    monkeypatch.setattr(core.templates, 'run', lambda root, tier: calls.append(('higgs', tier)) or 1)
    monkeypatch.setattr(core.pippit, 'run', lambda root: calls.append(('pippit', 'video')) or 0)
    assert core.main(['--root', str(radar)]) == 1
    assert calls == [('higgs', 'core'), ('pippit', 'video')]


def test_daily_consumes_pippit_after_configured_sitemap_sources(radar, monkeypatch):
    calls = []
    monkeypatch.setattr(daily.scheduled_scan, 'run', lambda root: calls.append('sitemaps') or 0)
    monkeypatch.setattr(daily.templates, 'run', lambda root, tier: calls.append('higgs') or 0)
    monkeypatch.setattr(daily.pippit, 'run', lambda root: calls.append('pippit') or 1)
    assert daily.main(['--root', str(radar)]) == 1
    assert calls == ['sitemaps', 'higgs', 'pippit']


def test_daily_snapshots_and_execution_are_bound_before_shared_latest_can_change(radar, monkeypatch):
    snapshots={}
    def scan(root,snapshot,execution_id):
        assert execution_id=='native-id';snapshot.update(run_id='native-id',status='ok');return 0
    def higgs(root,tier,snapshot):
        assert tier=='weekly';snapshot.update(status='ok',tier=tier);return 0
    def pip(root,snapshot):snapshot.update(status='ok');return 0
    monkeypatch.setattr(daily.scheduled_scan,'run',scan)
    monkeypatch.setattr(daily.templates,'run',higgs)
    monkeypatch.setattr(daily.pippit,'run',pip)
    assert daily.main(['--root',str(radar),'--tier','weekly'],snapshots=snapshots,execution_id='native-id')==0
    assert snapshots['scan']['run_id']=='native-id'
    assert snapshots['latest-template-scan.json']['tier']=='weekly'
