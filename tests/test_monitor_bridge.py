from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar import monitor_bridge as bridge

NOW = datetime(2026, 10, 19, 1, tzinfo=timezone.utc)
URL = 'https://example.test/tools/one'
NEW = 'https://example.test/tools/two'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def fixture(tmp_path):
    root, state, output = (tmp_path / name for name in ('monitor', 'bridge-state', 'output'))
    source = {'id': 'example', 'enabled': True, 'sitemap_urls': ['https://example.test/sitemap.xml']}
    write(root / 'monitor/config.json', {'sources': [source]})
    scan = {'status': 'ok', 'scan_phase': 'complete', 'run_id': 'run-1',
            'started_at': (NOW - timedelta(hours=1)).isoformat(), 'observed_at': NOW.isoformat(),
            'source_receipt_files': {'example': 'batch-runs/run-1/receipt.json'}}
    write(root / 'latest-scan.json', scan)

    def commit(urls=(URL,), *, scope_source=None, when=None, added=()):
        scope = {k: sorted(set((source if scope_source is None else scope_source).get(k, []))) for k in
                 ('sitemap_urls', 'include_path_regex', 'exclude_path_regex')}
        snapshot = {'source_id': 'example', 'scope_hash': bridge.hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
                    'observed_at': (when or NOW).isoformat(),
                    'baseline': {u: {'url': u, 'first_seen': '2020-01-01T00:00:00+00:00', 'lastmod': '2030-01-01'} for u in urls}}
        write(root / 'monitor/state/example.json', snapshot)
        receipt = {'status': 'committed', 'source_id': 'example', 'state_sha256': bridge.digest(snapshot),
                   'result': {'id': 'example', 'status': 'ok', 'observed_at': snapshot['observed_at'],
                              'added': [{'url': u} for u in added]}}
        write(root / 'batch-runs/run-1/receipt.json', receipt)
        return snapshot

    def run(**kwargs):
        return bridge.run_bridge(root, state, output, now=NOW, **kwargs)

    commit()
    return SimpleNamespace(root=root, state=state, output=output, source=source, scan=scan, commit=commit, run=run)


def test_first_baseline_zero_then_additions_and_repeat(fixture):
    f = fixture
    first = f.run()
    assert first['addition_count'] == 0
    assert first['sources'][0]['status'] == 'baseline_created'
    f.commit((URL, NEW), added=(NEW,))
    second = f.run()
    assert second['addition_count'] == 1
    assert second['sources'][0]['additions'][0] == {'url': NEW, 'first_seen': '2020-01-01T00:00:00+00:00', 'lastmod': '2030-01-01'}
    assert second['monitor_run_addition_count'] == 1
    assert f.run()['addition_count'] == 0
    assert 'not publication dates' in (f.output / 'sitemap-monitor.md').read_text()


@pytest.mark.parametrize('kind,expected', [('failed', 'uncommitted'), ('running', 'running'),
    ('partial', 'not_checked'), ('stale', 'stale'), ('digest', 'uncommitted'),
    ('scope_mismatch', 'scope_mismatch'), ('corrupt', 'malformed')])
def test_bad_sources_preserve_previous(fixture, kind, expected):
    f = fixture
    f.run()
    before = (f.state / 'baseline.json').read_bytes()
    f.commit((URL, NEW), added=(NEW,))
    receipt_path = f.root / 'batch-runs/run-1/receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if kind == 'failed':
        receipt['status'] = 'failed'
        write(receipt_path, receipt)
    elif kind == 'running':
        f.scan.update(status='running', scan_phase='running')
        write(f.root / 'latest-scan.json', f.scan)
    elif kind == 'partial':
        f.scan.update(status='partial', source_receipt_files={})
        write(f.root / 'latest-scan.json', f.scan)
    elif kind == 'stale':
        f.commit((URL, NEW), when=NOW - timedelta(hours=40))
        f.scan['started_at'] = (NOW - timedelta(hours=41)).isoformat()
        write(f.root / 'latest-scan.json', f.scan)
    elif kind == 'digest':
        receipt['state_sha256'] = 'bad'
        write(receipt_path, receipt)
    elif kind == 'scope_mismatch':
        f.commit((URL, NEW), scope_source={})
    else:
        (f.root / 'monitor/state/example.json').write_text('{')
    report = f.run()
    assert report['status'] == 'degraded'
    assert report['sources'][0]['status'] == expected
    assert report['addition_count'] == 0
    assert (f.state / 'baseline.json').read_bytes() == before


def test_partial_accepts_only_committed_sources(fixture):
    f = fixture
    config = {'sources': [f.source, dict(f.source, id='other')]}
    write(f.root / 'monitor/config.json', config)
    f.scan['status'] = 'partial'
    write(f.root / 'latest-scan.json', f.scan)
    report = f.run()
    assert (report['accepted_count'], report['source_count']) == (1, 2)
    assert report['status'] == 'degraded'


def test_scope_change_resets(fixture):
    f = fixture
    f.run()
    f.source['include_path_regex'] = ['/tools/']
    write(f.root / 'monitor/config.json', {'sources': [f.source]})
    f.commit((URL, NEW))
    report = f.run()
    assert report['sources'][0]['status'] == 'scope_reset'
    assert report['addition_count'] == 0
    assert f.run()['sources'][0]['status'] == 'ok'


@pytest.mark.parametrize('source', [None, {}, {'id': '../escape'}, {'id': ['bad']}])
def test_malformed_source(fixture, source):
    f = fixture
    write(f.root / 'monitor/config.json', {'sources': [source]})
    report = f.run()
    assert report['status'] == 'degraded'
    assert report['sources'][0]['status'] == 'malformed'
    assert not (f.root.parent / 'escape.json').exists()


@pytest.mark.parametrize('relative', ['../outside.json', '/etc/passwd'])
def test_receipt_traversal(fixture, relative):
    f = fixture
    f.scan['source_receipt_files']['example'] = relative
    write(f.root / 'latest-scan.json', f.scan)
    assert f.run()['sources'][0]['status'] == 'malformed'


@pytest.mark.parametrize('input_name', ['monitor/config.json', 'latest-scan.json'])
@pytest.mark.parametrize('mode', ['missing', 'corrupt'])
def test_unavailable_global_input_preserves_state(fixture, input_name, mode):
    f = fixture
    f.run()
    before = (f.state / 'baseline.json').read_bytes()
    path = f.root / input_name
    if mode == 'missing':
        path.unlink()
    else:
        path.write_text('{')
    assert f.run()['status'] == 'degraded'
    assert (f.state / 'baseline.json').read_bytes() == before


def test_corrupt_bridge_state_is_not_silently_reset(fixture):
    f = fixture
    f.run()
    (f.state / 'baseline.json').write_text('{')
    assert f.run()['status'] == 'degraded'
    assert (f.state / 'baseline.json').read_text() == '{'


def test_metadata_race_discards_entire_update(fixture, monkeypatch):
    f = fixture
    f.run()
    before = (f.state / 'baseline.json').read_bytes()
    f.commit((URL, NEW))
    verify = bridge.Inputs.verify

    def race(inputs):
        write(f.root / 'latest-scan.json', dict(f.scan, run_id='run-2'))
        verify(inputs)

    monkeypatch.setattr(bridge.Inputs, 'verify', race)
    assert f.run()['status'] == 'degraded'
    assert (f.state / 'baseline.json').read_bytes() == before


def test_never_writes_monitor_tree_or_fetches(fixture, monkeypatch):
    f = fixture
    before = {p: p.read_bytes() for p in f.root.rglob('*') if p.is_file()}
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network called'))
    f.run()
    f.run()
    assert {p: p.read_bytes() for p in f.root.rglob('*') if p.is_file()} == before
    with pytest.raises(bridge.BridgeError):
        bridge.run_bridge(f.root, f.root / 'bad-state', f.output)
    with pytest.raises(bridge.BridgeError):
        bridge.run_bridge(f.root, f.state, f.root / 'bad-output')


def test_simultaneous_run_preserves_outputs(fixture):
    f = fixture
    f.run()
    before = (f.output / 'sitemap-monitor.json').read_bytes()
    with (f.state / 'bridge.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(bridge.BridgeError, match='busy'):
            f.run()
    assert (f.output / 'sitemap-monitor.json').read_bytes() == before


def test_exact_url_identity_no_language_dedup(fixture):
    f = fixture
    f.run()
    translated = 'https://example.test/zh/tools/one'
    f.commit((URL, translated))
    assert f.run()['addition_count'] == 1


def test_cli_is_standalone(fixture, capsys):
    f = fixture
    assert bridge.main(['--monitor-root', str(f.root), '--state', str(f.state), '--output', str(f.output),
                        '--max-age-hours', '999999']) in (0, 1)
    assert 'accepted_count' in capsys.readouterr().out


@pytest.fixture
def daily(fixture, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/new_term_radar_daily.py'
    spec = importlib.util.spec_from_file_location('daily_wrapper', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE', fixture.state.parent / 'daily-state')
    monkeypatch.setattr(module, 'OUTPUT', fixture.output)
    monkeypatch.setattr(module, 'MONITOR', fixture.root)
    return module


@pytest.mark.parametrize('failure', [False, True])
def test_daily_integration_report_pairs(daily, monkeypatch, failure):
    def subprocess_run(command, **kwargs):
        if command[:2] == ['gh', 'auth']:
            return SimpleNamespace(returncode=1, stdout='')
        assert command[1:4] == ['-m', 'radar', 'daily']
        if failure:
            raise daily.subprocess.TimeoutExpired(command, 300)
        out = Path(command[command.index('--out') + 1])
        out.write_text('Free sources report')
        write(out.with_suffix('.json'), {'leads': []})
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(daily.subprocess, 'run', subprocess_run)
    if failure:
        with pytest.raises(SystemExit):
            daily.main()
    else:
        daily.main()
    reports = list(daily.OUTPUT.glob('radar-*.json'))
    assert len(reports) == 1
    data = json.loads(reports[0].read_text())
    assert 'monitor_bridge' in data
    assert data == json.loads((daily.OUTPUT / 'latest.json').read_text())
    assert reports[0].with_suffix('.md').read_text() == (daily.OUTPUT / 'latest.md').read_text()
    assert (daily.OUTPUT / 'sitemap-monitor.json').exists()
    if failure:
        assert data['status'] == 'degraded'


def test_daily_bridge_exception_explicit_degradation(daily, monkeypatch):
    def fail(*args):
        raise bridge.BridgeError('private error not printed')
    monkeypatch.setattr(daily, 'run_bridge', fail)
    result = daily.bridge_report()
    assert result['status'] == 'degraded'
    assert 'private' not in str(result)
