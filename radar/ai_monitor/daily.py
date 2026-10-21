"""Daily configured-source collection plus supplemental templates, without delivery."""
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime import DEFAULT_ROOT
from . import scheduled_scan, templates, pippit


def main(argv=None, snapshots=None, execution_id=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--tier', choices=['core', 'daily', 'weekly'])
    args = parser.parse_args(argv)
    try:
        kwargs = {} if snapshots is None else {'snapshot':snapshots.setdefault('scan', {})}
        if execution_id is not None:
            kwargs['execution_id'] = execution_id
        scanned = scheduled_scan.run(args.root, **kwargs)
        tier = args.tier or ('weekly' if datetime.now(ZoneInfo('Asia/Shanghai')).weekday() == 6 else 'daily')
        kwargs = {} if snapshots is None else {'snapshot':snapshots.setdefault('latest-template-scan.json', {})}
        templated = templates.run(args.root, tier, **kwargs)
        kwargs = {} if snapshots is None else {'snapshot':snapshots.setdefault('latest-pippit-template-scan.json', {})}
        pip = pippit.run(args.root, **kwargs) if pippit.configured(args.root) else 0
        return 1 if scanned or templated or pip else 0
    except scheduled_scan.ScanBusy as error:
        import json
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
