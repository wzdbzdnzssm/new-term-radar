import hashlib
import json
from pathlib import Path

import pytest

from radar.ai_monitor import queue_store as store, templates, daily_scan


@pytest.mark.parametrize('value', ['', '你好', {'b': [None, True, -3, 1.2, 'Bulin 47', '舞蹈'], 'a': {'x': 42}}, {'n': float('inf')}])
def test_digest_preserves_existing_receipt_contract(value):
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert store.digest(value) == expected


def test_transaction_streams_hash_and_recovers_every_consumer(tmp_path, monkeypatch):
    payload = {'observations': [{'id': i, 'name': '模板' * 50} for i in range(100)]}
    original_write = store.write
    failed = False

    def fail_after_first(path, value):
        nonlocal failed
        if Path(path).name == 'receipt.json' and not failed:
            failed = True
            raise OSError('injected disk interruption')
        return original_write(path, value)

    monkeypatch.setattr(store, 'write', fail_after_first)
    # dumps used to allocate the whole large journal twice. The transaction must
    # now hash and write without that allocation, including during replay.
    monkeypatch.setattr(store.json, 'dumps', lambda *a, **k: (_ for _ in ()).throw(AssertionError('whole JSON allocation')))
    with pytest.raises(OSError):
        store.commit(tmp_path, 'scan', [store.op('pending.json', payload), store.op('receipt.json', {'status': 'committed'})])
    store.recover(tmp_path)
    assert store.load(tmp_path / 'pending.json', {}) == payload
    assert store.load(tmp_path / 'receipt.json', {})['status'] == 'committed'
    before = (tmp_path / 'pending.json').read_bytes()
    store.recover(tmp_path)
    assert (tmp_path / 'pending.json').read_bytes() == before


def test_scope_reset_keeps_historical_first_seen():
    source = {'id': 'higgsfield-ai', 'sitemap_urls': ['https://higgsfield.ai/motion/sitemap.xml']}
    url = 'https://higgsfield.ai/motion/template'
    state, _ = store.monitor.reconcile(source, None, {url: {'lastmod': None}}, '2026-01-01T00:00:00+00:00')
    source['sitemap_urls'].append('https://higgsfield.ai/effects/sitemap.xml')
    state, result = store.monitor.reconcile(source, state, {url: {'lastmod': None}}, '2026-10-20T00:00:00+00:00')
    assert state['baseline'][url]['first_seen'] == '2026-01-01T00:00:00+00:00'
    assert result['added'] == []


def test_single_uuid_and_new_card_on_unchanged_aggregate_url(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'MAPS', {'core': ['motion/sitemap.xml'], 'daily': [], 'weekly': []})
    monkeypatch.setattr(templates, 'AGGREGATES', ['/effects'])
    first = 'https://higgsfield.ai/motion/11111111-1111-1111-1111-111111111111'
    second = 'https://higgsfield.ai/motion/22222222-2222-2222-2222-222222222222'
    urls = [first]
    cards = [first]
    names = {first: 'Hotel Lobby', second: 'Bulin 47'}

    def sitemap(url, options):
        return ('<urlset>' + ''.join('<url><loc>' + value + '</loc></url>' for value in urls) + '</urlset>').encode()

    def page(url):
        html = '<h1>' + names.get(url, 'Effects') + '</h1>'
        if url.endswith('/effects'):
            html += ''.join('<a href="' + value + '"><h2>' + names[value] + '</h2></a>' for value in cards)
        return {'url': url, 'valid': True, 'raw_html': html, 'summary': 'Upload a photo to make this dance video', 'title': 'fallback'}

    assert templates.run(tmp_path, fetcher=sitemap, page_reader=page) == 0
    assert store.load(tmp_path / 'latest-template-scan.json', {})['events'] == []
    assert list((tmp_path/'supply-deltas').glob('*.json')) == []
    cards.append(second)  # no URL added to the sitemap, aggregate URL unchanged
    assert templates.run(tmp_path, fetcher=sitemap, page_reader=page) == 0
    events = store.load(tmp_path / 'latest-template-scan.json', {})['events']
    assert len(events) == 1 and events[0]['name'] == 'Bulin 47'
    report=store.load(tmp_path/'latest-template-scan.json',{})
    ref=report['aggregates'][0]['supply_delta'];delta=store.load(tmp_path/ref['path'],{})
    assert delta['added_urls']==[second] and store.digest(delta)==ref['sha256']
    pool = store.load(tmp_path / 'video-template-candidates.json', {})
    candidate = pool['entities'][templates.identity(second)]
    assert candidate['handoff']['independent_demand'] == 'unverified'
    assert candidate['handoff']['implementation']['launch_ready'] is False
    assert 'raw_html' not in str(pool)
    assert templates.run(tmp_path, fetcher=sitemap, page_reader=page) == 0
    assert store.load(tmp_path / 'latest-template-scan.json', {})['events'] == []
    urls.append(second)
    templates.run(tmp_path, fetcher=sitemap, page_reader=page)
    # Same UUID seen in detail is one entity, despite card/detail routes.
    assert len(store.load(tmp_path / 'video-template-candidates.json', {})['entities']) == 2
    assert store.load(tmp_path / 'latest-template-scan.json', {})['events'] == []
    assert 'supply_delta' not in store.load(tmp_path/'latest-template-scan.json',{})['feeds'][0]
    assert len(store.load(tmp_path / 'pending-observations.json', {})['observations']) == 1


def test_supplemental_change_enters_existing_review_queue(tmp_path, monkeypatch):
    from radar.ai_monitor import review_pages
    monkeypatch.setattr(templates, 'MAPS', {'core': ['blog/sitemap.xml'], 'daily': [], 'weekly': []})
    monkeypatch.setattr(templates, 'AGGREGATES', [])
    urls = ['https://higgsfield.ai/blog/old']
    def sitemap(url, options):
        return ('<urlset>' + ''.join('<url><loc>' + u + '</loc></url>' for u in urls) + '</urlset>').encode()
    def page(url):
        record = review_pages.parse_html('<h1>Bulin 47</h1><p>' + 'Upload your photo to create a freestyle video. ' * 8 + '</p>', url)
        record.update(url=url, final_url=url, valid=True, http_status=200)
        return record
    templates.run(tmp_path, fetcher=sitemap, page_reader=page)
    urls.append('https://higgsfield.ai/blog/new')
    assert templates.run(tmp_path, fetcher=sitemap, page_reader=page) == 0
    report=store.load(tmp_path/'latest-template-scan.json',{})
    ref=report['feeds'][0]['supply_delta'];delta=store.load(tmp_path/ref['path'],{})
    assert store.digest(delta)==ref['sha256'] and delta['added_urls']==[urls[-1]]
    assert delta['baseline_created'] is False and delta['before_observed_at']
    rows = store.load(tmp_path / 'pending-observations.json', {})['observations']
    assert len(rows) == 1 and rows[0]['review_lane'] == 'supplemental_evidence'
    evidence = store.load(tmp_path / rows[0]['evidence'][0]['evidence_file'], {})
    assert evidence['content_verified'] is True
    assert len(evidence['summary']) <= 500 and 'raw_html' not in evidence and 'text' not in evidence


def test_locale_and_uuid_identity_and_reference_unknown():
    url = 'https://higgsfield.ai/motion/11111111-1111-1111-1111-111111111111'
    assert templates.identity(url) == templates.identity(url.replace('/motion/', '/de/motion/'))


def test_known_empty_feed_and_capability_page_are_explicit(tmp_path, monkeypatch):
    from radar.ai_monitor import review_pages
    monkeypatch.setattr(templates, 'MAPS', {'core': ['contests/sitemap.xml'], 'daily': [], 'weekly': []})
    monkeypatch.setattr(templates, 'AGGREGATES', ['/genjutsu'])
    def page(url):
        record = review_pages.parse_html('<h1>Genjutsu</h1><p>' + 'Fixed motion transfer capability. ' * 8 + '</p>', url)
        return dict(record, url=url, valid=True, http_status=200)
    assert templates.run(tmp_path, fetcher=lambda *a: b'<urlset/>', page_reader=page) == 0
    report = store.load(tmp_path/'latest-template-scan.json',{})
    assert report['feeds'][0]['url_count'] == 0
    assert report['aggregates'][0]['catalog_available'] is False
    assert report['events'] == []
    assert store.load(tmp_path/'pending-observations.json',{})['observations'] == []


def test_commit_failure_retains_exception_type_in_worker_receipt(tmp_path, monkeypatch):
    store.write(tmp_path/'monitor/config.json', {'sources': [{'id': 'pollo-ai'}]})
    monkeypatch.setattr(daily_scan, 'collect', lambda *a, **k: ({}, {'id': 'pollo-ai', 'status': 'ok'}, []))
    def fail(*args, **kwargs):
        raise MemoryError()
    monkeypatch.setattr(daily_scan, 'commit', fail)
    assert daily_scan.run(tmp_path, ['pollo-ai'], receipt_path='receipt.json') == 1
    result = store.load(tmp_path/'receipt.json',{})['result']
    assert result['error_type'] == 'MemoryError'
    assert 'MemoryError' in result['error']


def test_worker_does_not_reload_pending_after_durable_commit(tmp_path, monkeypatch):
    store.write(tmp_path/'monitor/config.json', {'sources': [{'id': 'pollo-ai'}]})
    monkeypatch.setattr(daily_scan, 'collect', lambda *a, **k: ({}, {'id': 'pollo-ai', 'status': 'ok'}, []))
    original = daily_scan.load
    reads = 0
    def bounded_load(path, default):
        nonlocal reads
        if path.name == 'pending-observations.json':
            reads += 1
            if reads > 1:
                raise MemoryError('duplicate post-commit read')
        return original(path, default)
    monkeypatch.setattr(daily_scan, 'load', bounded_load)
    assert daily_scan.run(tmp_path, ['pollo-ai'], receipt_path='receipt.json') == 0
    assert reads == 1
    assert store.load(tmp_path/'receipt.json',{})['status'] == 'committed'


@pytest.mark.parametrize('redirects', [False, True, None, 'canonical'])
def test_removed_page_evidence_only_attaches_to_its_matching_entity(monkeypatch, redirects):
    source={'id':'higgsfield-ai','sitemap_urls':['https://higgsfield.ai/sitemap.xml']}
    old='https://higgsfield.ai/effects/old'
    unrelated='https://higgsfield.ai/effects/unrelated'
    new='https://higgsfield.ai/effects/new'
    previous,_=store.monitor.reconcile(source,None,{old:{'lastmod':None},unrelated:{'lastmod':None}},'2026-01-01T00:00:00+00:00')
    monkeypatch.setattr(store.monitor,'crawl',lambda *a: ({new:{'lastmod':None}},[]))
    def evidence(url):
        if url==old and redirects is None:
            return {'url':url,'valid':False,'error':'source page unavailable'}
        return {'url':url,'valid':True,'title':'Video template','final_url':new if url==old and redirects is True else url,
                'html_canonical':new if url==old and redirects=='canonical' else None}
    _,result,rows=daily_scan.collect(source,{},previous,evidence_fetcher=evidence)
    assert len(rows)==1
    assert {row['url'] for row in rows[0]['evidence']} == ({new,old} if redirects else {new})
    assert unrelated not in rows[0]['evidence_urls']
    assert rows[0]['first_seen']==('2026-01-01T00:00:00+00:00' if redirects else rows[0]['observed_at'])
    assert len(result['migration_evidence_errors'])==(1 if redirects is None else 0)
    assert rows[0]['evidence_errors']==[]


def test_removed_aliases_match_only_their_entity_among_multiple_new_groups(monkeypatch):
    source={'id':'higgsfield-ai','sitemap_urls':['https://higgsfield.ai/sitemap.xml']}
    old=['https://higgsfield.ai/effects/old-one','https://higgsfield.ai/effects/old-two']
    new=['https://higgsfield.ai/effects/new-one','https://higgsfield.ai/effects/new-two']
    previous,_=store.monitor.reconcile(source,None,{url:{'lastmod':None} for url in old},'2026-01-01T00:00:00+00:00')
    monkeypatch.setattr(store.monitor,'crawl',lambda *a: ({url:{'lastmod':None} for url in new},[]))
    def evidence(url):
        return {'url':url,'valid':True,'title':'Video template','final_url':url,'html_canonical':new[0] if url in old else None}
    _,_,rows=daily_scan.collect(source,{},previous,evidence_fetcher=evidence)
    grouped={row['canonical_path']:row for row in rows}
    migrated=grouped['/effects/new-one']
    assert set(migrated['migrated_from'])==set(old)
    assert set(migrated['aliases'])==set(old+[new[0]])
    assert migrated['first_seen']=='2026-01-01T00:00:00+00:00'
    assert {row['url'] for row in grouped['/effects/new-two']['evidence']}=={new[1]}


def test_failed_aggregate_preserves_snapshot_and_is_not_no_change(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, 'MAPS', {'core': [], 'daily': [], 'weekly': []})
    monkeypatch.setattr(templates, 'AGGREGATES', ['/effects'])
    previous = {'schema_version': 1, 'feeds': {}, 'aggregates': {'/effects': {'cards': {'old': 'fingerprint'}}}}
    store.write(tmp_path / 'template-state.json', previous)
    assert templates.run(tmp_path, page_reader=lambda url: {'url': url, 'valid': True, 'raw_html': '<h1>Client rendered catalog</h1>'}) == 1
    assert store.load(tmp_path / 'template-state.json', {}) == previous
    assert store.load(tmp_path / 'latest-template-scan.json', {})['aggregates'][0]['status'] == 'unread'
