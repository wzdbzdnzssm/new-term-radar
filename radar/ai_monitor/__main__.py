"""Unified AI opportunity commands. No command sends external messages."""
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {'daily': 'daily', 'core': 'core', 'pippit': 'pippit', 'scan': 'scheduled_scan', 'review': 'review_queue',
                'evidence': 'review_pages', 'templates': 'templates',
                'compact': 'compact_evidence'}
    if not args or args[0] not in commands:
        print('Usage: python -m radar.ai_monitor {daily,core,scan,templates,pippit,review,evidence,compact} [options]', file=sys.stderr)
        return 2
    from importlib import import_module
    return import_module('.' + commands[args.pop(0)], __package__).main(args)


if __name__ == '__main__':
    raise SystemExit(main())
