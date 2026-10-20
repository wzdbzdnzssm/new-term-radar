"""Replace stored page bodies with compact observations, one atomic file at a time."""
import argparse
import json
from pathlib import Path

from .evidence_metadata import compact, metadata_valid
from .queue_store import load, write, locked, recover
from .scheduled_scan import batch_lock


def manual_references(root):
    found = {}

    def visit(value):
        if isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            for row in value.get('historical_demand_evidence', []):
                if (isinstance(row, dict) and row.get('evidence_file') and row.get('original')
                        and isinstance(row.get('text_offset'), int) and row.get('scope') == 'same_ai_video_need'):
                    bucket = found.setdefault(row['evidence_file'], [])
                    if row not in bucket:
                        bucket.append(row)
            for key, row in value.items():
                if key != 'historical_demand_evidence' and isinstance(row, (dict, list)):
                    visit(row)

    paths = [root / 'opportunity-registry.json', root / 'pending-observations.json']
    paths.extend((root / 'outbox').glob('*.json'))
    for path in paths:
        visit(load(path, {}))
    return found


def transform(value, manual=()):
    if isinstance(value, dict):
        if ('raw_html' in value or 'text' in value or value.get('storage_version')) and 'url' in value:
            return compact(value, manual)
        return {key: transform(row) for key, row in value.items()}
    if isinstance(value, list):
        return [transform(row) for row in value]
    return value


def run(root, apply=False):
    root = root.resolve()
    if not root.is_dir() or not (root / 'page-evidence').is_dir():
        raise ValueError('Expected an existing monitor root and page-evidence directory')
    counts = {'files': 0, 'changed': 0, 'before_bytes': 0, 'after_bytes': 0, 'apply': apply}
    # Same lock ordering as scheduled_scan; a live scan prevents maintenance entirely.
    with batch_lock(root), locked(root):
        if apply:
            recover(root)
        manual = manual_references(root)
        text_sources = {}
        for index in (root / 'page-evidence').rglob('index.json'):
            rows = load(index, [])
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get('url') and str(row.get('file', '')).endswith('.txt'):
                        text_sources[index.parent / Path(row['file']).name] = row
        paths = [p for p in (root / 'page-evidence').rglob('*') if p.is_file() and p.suffix in {'.json', '.txt'}]
        for path in sorted(paths):
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError('Evidence path outside monitor root')
            before = path.stat().st_size
            relative = path.relative_to(root).as_posix()
            if path.suffix == '.txt':
                if path not in text_sources:
                    raise ValueError('Legacy text page has no URL metadata index: ' + str(path))
                # After the first migration a .txt path already contains compact JSON.
                raw = path.read_text(encoding='utf-8')
                try:
                    old = json.loads(raw)
                except ValueError:
                    old = dict(text_sources[path], text=raw, evidence_file=relative)
                else:
                    if not isinstance(old, dict) or not metadata_valid(old):
                        raise ValueError('Legacy text contains unsupported JSON instead of compact metadata: ' + str(path))
                new = transform(old, manual.get(relative, ()))
            else:
                old = load(path, {})
                new = transform(old, manual.get(relative, ()))
            counts['files'] += 1
            counts['before_bytes'] += before
            if new != old:
                counts['changed'] += 1
                if apply:
                    write(path, new)
                    # Verify the exact durable representation before proceeding.
                    if load(path, None) != new:
                        raise OSError('Compacted page read-back mismatch')
            counts['after_bytes'] += (path.stat().st_size if apply else
                                     len((json.dumps(new, ensure_ascii=False, indent=2) + '\n').encode()))
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(run(args.root, args.apply)))


if __name__ == '__main__':
    main()
