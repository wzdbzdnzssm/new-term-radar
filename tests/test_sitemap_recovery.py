import json
from pathlib import Path

import pytest

from radar.ai_monitor import daily_scan, run_monitor as monitor


def xml(*urls, kind='urlset'):
    child = 'sitemap' if kind == 'sitemapindex' else 'url'
    return ('<' + kind + '>' + ''.join('<' + child + '><loc>' + u + '</loc></' + child + '>' for u in urls) + '</' + kind + '>').encode()


def source():
    return {'id': 'site', 'enabled': True, 'sitemap_urls': ['https://example.com/old.xml'],
            'include_path_regex': ['video'], 'max_sitemaps': 5}


def test_recovery_updates_config_with_baseline_without_new_demand(tmp_path):
    s = source()
    config = {'sources': [s]}
    directory = tmp_path / 'monitor'
    directory.mkdir()
    (directory / 'config.json').write_text(json.dumps(config))
    old = {'https://example.com/video-old': {'lastmod': None}}
    state, _ = monitor.reconcile(s, None, old, '2026-01-01T00:00:00+00:00')
    (directory / 'state').mkdir()
    (directory / 'state/site.json').write_text(json.dumps(state))
    def fetch(url, options):
        if url.endswith('old.xml'): raise OSError('retired')
        if url.endswith('robots.txt'): return b'Sitemap: https://example.com/new.xml\nSitemap: http://127.0.0.1/private.xml'
        return xml('https://example.com/video-old', 'https://example.com/video-new', 'https://example.com/photo')
    # Private discovery fails closed without touching the old config or state.
    before = (directory / 'state/site.json').read_bytes()
    assert daily_scan.run(tmp_path, fetcher=fetch) == 1
    assert (directory / 'state/site.json').read_bytes() == before
    assert json.loads((directory / 'config.json').read_text()) == config
    def safe_fetch(url, options):
        if url.endswith('robots.txt'): return b'Sitemap: https://example.com/new.xml\nSitemap: https://other.example/map.xml'
        return fetch(url, options)
    assert daily_scan.run(tmp_path, fetcher=safe_fetch) == 0
    saved = json.loads((directory / 'config.json').read_text())['sources'][0]
    assert saved['sitemap_urls'] == ['https://example.com/new.xml']
    state = json.loads((directory / 'state/site.json').read_text())
    assert state['scope_hash'] == monitor.scope_hash(saved)
    assert state['baseline']['https://example.com/video-old']['first_seen'].startswith('2026-01-01')
    assert set(state['baseline']) == {'https://example.com/video-old', 'https://example.com/video-new'}
    assert json.loads((tmp_path / 'pending-observations.json').read_text())['observations'] == []


def test_failed_replacement_preserves_configuration():
    s = source()
    def fetch(url, options):
        if url.endswith('robots.txt'): return b'Sitemap: https://example.com/new.xml'
        return b'<urlset/>'
    with pytest.raises(monitor.CrawlFailure, match='robots recovery failed'):
        monitor.crawl(s, {}, fetch)
    assert s['sitemap_urls'] == ['https://example.com/old.xml']


def test_optional_empty_requires_robots_and_retains_scoped_history():
    s = source()
    s['sitemap_urls'] = ['https://example.com/main.xml', 'https://example.com/optional.xml']
    s['allow_empty_sitemap_urls'] = ['https://example.com/optional.xml']
    previous = {'baseline': {'https://example.com/video-historical': {'lastmod': 'old'},
                             'https://example.com/photo': {'lastmod': None}}}
    calls = []
    def fetch(url, options):
        calls.append(url)
        if url.endswith('robots.txt'):
            return b'Sitemap: https://example.com/main.xml\nSitemap: https://example.com/optional.xml'
        return xml('https://example.com/video-live') if url.endswith('main.xml') else xml()
    current, maps = monitor.crawl(s, {}, fetch, previous)
    assert set(current) == {'https://example.com/video-live', 'https://example.com/video-historical'}
    assert any(m['status'] == 'empty_preserved' for m in maps)
    assert 'https://example.com/robots.txt' in calls
    # HTTP failure or invalid/empty index is never an allowed empty inventory.
    for broken in [b'<html/>', xml(kind='sitemapindex')]:
        with pytest.raises(monitor.CrawlFailure):
            monitor.crawl(s, {}, lambda u, o: broken if u.endswith('optional.xml') else fetch(u, o), previous)
    def unavailable(url, options):
        if url.endswith('optional.xml'): raise OSError('network failure')
        return fetch(url, options)
    with pytest.raises(monitor.CrawlFailure, match='network failure'):
        monitor.crawl(s, {}, unavailable, previous)


def test_recovery_is_bounded_and_rejects_foreign_children():
    s = source()
    def fetch(url, options):
        if url.endswith('old.xml'): raise OSError('retired')
        if url.endswith('robots.txt'): return b'Sitemap: https://example.com/new.xml'
        return xml('https://other.example/child.xml', kind='sitemapindex')
    with pytest.raises(monitor.CrawlFailure, match='leaves configured origin'):
        monitor.crawl(s, {}, fetch)
    s['max_sitemaps'] = 1
    with pytest.raises(ValueError, match='count exceeds'):
        monitor.robots_sitemaps(s, {}, lambda u, o: b'Sitemap: https://example.com/a.xml\nSitemap: https://example.com/b.xml')


def test_renamed_map_is_not_silently_lost_with_other_valid_maps():
    s = json.loads((Path(__file__).parent / 'fixtures/picsart-source.json').read_text())
    s['sitemap_urls'] = ['https://picsart.com/retired.xml', 'https://picsart.com/tutorials/tutorials.xml']
    called = []
    def fetch(url, options):
        called.append(url)
        if url.endswith('retired.xml'): raise OSError('retired')
        if url.endswith('robots.txt'):
            return b'Sitemap: https://picsart.com/new-landings.xml\nSitemap: https://picsart.com/tutorials/tutorials.xml\nSitemap: https://picsart.com/fonts/fonts.xml'
        if url.endswith('new-landings.xml'): return xml('https://picsart.com/ai-video-generator/')
        if url.endswith('tutorials.xml'): return xml('https://picsart.com/tutorials/create-ai-video/')
        raise AssertionError('Unrelated font map should not be read')
    current, maps = monitor.crawl(s, {}, fetch)
    assert set(current) == {'https://picsart.com/ai-video-generator/', 'https://picsart.com/tutorials/create-ai-video/'}
    assert 'https://picsart.com/new-landings.xml' in called
    assert any(m['status'] == 'excluded_scope' and '/fonts/' in m['url'] for m in maps)


@pytest.mark.parametrize('url', ['http://10.0.0.1/x', 'http://169.254.169.254/x', 'http://[::1]/x', 'http://test.local/x'])
def test_private_urls_rejected(url):
    with pytest.raises(ValueError): monitor.checked_url(url)


def test_picsart_scope_covers_video_tutorials_and_models_without_image_noise():
    import re
    s = json.loads((Path(__file__).parent / 'fixtures/picsart-source.json').read_text())
    def included(path):
        return (any(re.search(p, path) for p in s['include_path_regex']) and
                not any(re.search(p, path) for p in s['exclude_path_regex']))
    for path in ['/tutorials/how-to-turn-photos-into-ai-animated-videos/',
                 '/tutorials/how-to-create-faceless-youtube-history-videos-with-picsart-storyline/',
                 '/ai-models/pixverse-c1-image/', '/ai-models/veo-3-1/',
                 '/zh/idea-to-movie', '/tutorials/create-product-assembly-animations-from-a-single-image/']:
        assert included(path), path
    for path in ['/fonts/foo/', '/colors/red/', '/videos/free-stock/',
                 '/ai-models/nano-banana-2/', '/ai-models/flux-3-image/',
                 '/ai-avatar/', '/ai-stylist/app', '/photo-effects/', '/stock-videos/']:
        assert not included(path), path
    assert len(s['sitemap_urls']) == 8
    assert 'https://picsart.com/miniapp-shell-sitemap.xml' in s['allow_empty_sitemap_urls']


def test_picsart_underscore_locale_alias_is_not_a_new_need():
    from radar.ai_monitor.queue_store import canonical_path
    s = {'id': 'picsart-com'}
    assert canonical_path('https://picsart.com/zh_HANT/idea-to-movie', s) == '/idea-to-movie'
    assert canonical_path('https://picsart.com/zh/idea-to-movie', s) == '/idea-to-movie'
