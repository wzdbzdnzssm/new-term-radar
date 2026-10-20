from datetime import timedelta
import json

import pytest

from test_monitor_bridge import fixture, NOW, URL, NEW, write


@pytest.mark.parametrize('kind', ['progress_running', 'old_receipt', 'future', 'malformed_url', 'duplicate_id'])
def test_additional_input_guards(fixture, kind):
    f = fixture
    f.run()
    before = (f.state / 'baseline.json').read_bytes()
    if kind == 'progress_running':
        f.scan['progress_file'] = 'batch-runs/run-1/progress.json'
        write(f.root / f.scan['progress_file'], {'run_id': 'run-1', 'phase': 'running'})
        write(f.root / 'latest-scan.json', f.scan)
    elif kind == 'old_receipt':
        f.scan['source_receipt_files']['example'] = 'batch-runs/run-0/receipt.json'
        write(f.root / 'latest-scan.json', f.scan)
    elif kind == 'future':
        f.commit((URL, NEW), when=NOW + timedelta(hours=1))
    elif kind == 'malformed_url':
        f.commit((URL, 'javascript:alert(1)'))
    else:
        write(f.root / 'monitor/config.json', {'sources': [f.source, f.source]})
    report = f.run()
    assert report['status'] == 'degraded'
    assert report['addition_count'] == 0
    assert (f.state / 'baseline.json').read_bytes() == before
