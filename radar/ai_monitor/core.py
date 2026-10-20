"""Four-hour silent discovery; the daily review owns all notifications."""
import argparse
import json
from pathlib import Path

from . import pippit, templates
from .runtime import DEFAULT_ROOT
from .scheduled_scan import ScanBusy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        higgs = templates.run(args.root, 'core')
        pip = pippit.run(args.root) if pippit.configured(args.root) else 0
        return 1 if higgs or pip else 0
    except ScanBusy as error:
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
