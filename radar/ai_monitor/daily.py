"""Daily configured-source collection plus supplemental templates, without delivery."""
import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime import DEFAULT_ROOT
from . import scheduled_scan, templates, pippit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        scanned = scheduled_scan.run(args.root)
        tier = 'weekly' if datetime.now(ZoneInfo('Asia/Shanghai')).weekday() == 6 else 'daily'
        templated = templates.run(args.root, tier)
        pip = pippit.run(args.root) if pippit.configured(args.root) else 0
        return 1 if scanned or templated or pip else 0
    except scheduled_scan.ScanBusy as error:
        import json
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
