"""Daily 58-source collection plus supplemental template evidence, without delivery."""
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime import DEFAULT_ROOT
from . import scheduled_scan, templates


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        scanned = scheduled_scan.run(args.root)
        tier = 'weekly' if datetime.now(ZoneInfo('Asia/Shanghai')).weekday() == 6 else 'daily'
        templated = templates.run(args.root, tier)
        return 1 if scanned or templated else 0
    except scheduled_scan.ScanBusy as error:
        import json
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
