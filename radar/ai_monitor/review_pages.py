"""Collect compact public-page observations without persisting HTML or body text."""
import argparse
import html.parser
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urljoin
from .queue_store import monitor, digest, load, write, locked, recover, evidence_reference
from .evidence_metadata import compact, prepare_history, verified
from .runtime import DEFAULT_ROOT
ROOT = DEFAULT_ROOT

class Text(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []; self.title_parts = []; self.in_title = False; self.canonical = None; self.media = set()
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'noscript'): self.skip += 1
        if tag == 'title': self.in_title = True
        if tag == 'link' and 'canonical' in attrs.get('rel', '').lower().split(): self.canonical = attrs.get('href')
        if tag in ('video', 'audio', 'img'): self.media.add(tag)
        if tag == 'meta' and attrs.get('property', '').startswith('og:video'): self.media.add('video')
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'): self.skip = max(0, self.skip - 1)
        if tag == 'title': self.in_title = False
    def handle_data(self, data):
        if self.in_title: self.title_parts.append(data.strip())
        if not self.skip and data.strip(): self.parts.append(data.strip())

def parse_html(text, final_url):
    parser = Text(); parser.feed(text)
    body = '\n'.join(parser.parts)
    canonical = urljoin(final_url, parser.canonical) if parser.canonical else None
    if canonical: monitor.checked_url(canonical)
    return {'title': ' '.join(parser.title_parts), 'text': body, 'summary': re.sub(r'\s+', ' ', body)[:1600], 'content_hash': digest(body), 'html_canonical': canonical, 'media_types': sorted(parser.media), 'raw_html': text, 'raw_html_hash': digest(text)}

def read_page(url, opener=None):
    record = {'url': url, 'checked_at': monitor.utc_now(), 'valid': False}
    try:
        monitor.checked_url(url)
        client = opener or urllib.request.build_opener(monitor.CheckedRedirect())
        with client.open(urllib.request.Request(url, headers={'User-Agent': 'SitemapTrendMonitor/2.0 evidence'}), timeout=25) as response:
            raw = response.read(5000001)
            if len(raw) > 5000000: raise ValueError('Evidence HTML exceeds 5MB')
            final = monitor.checked_url(response.geturl())
            if 'html' not in response.headers.get('Content-Type', '').lower(): raise ValueError('Evidence is not HTML')
            data = parse_html(raw.decode(response.headers.get_content_charset() or 'utf-8', errors='replace'), final)
            record.update(data, http_status=response.status, final_url=final, valid=response.status == 200 and len(data['text']) > 100)
    except MemoryError:
        raise  # Resource exhaustion must preserve the source baseline for retry.
    except Exception as error:
        record['error'] = str(error)
    return record

def fetch(url, root=ROOT, opener=None):
    record = read_page(url, opener)
    relative = 'page-evidence/' + digest(record) + '.json'
    record['evidence_file'] = relative
    record = compact(record)
    write(root / relative, record)
    return record

def verify_history(url, requests, root=ROOT, opener=None):
    record = read_page(url, opener)
    relative = 'page-evidence/' + digest(record) + '.json'
    record['evidence_file'] = relative
    checked = prepare_history(record, requests)
    persisted = compact(record, checked)
    write(root / relative, persisted)
    return {'evidence': evidence_reference(persisted), 'historical_demand_evidence': checked}


def historical_subject(root, observation_id):
    """Return only hash-verified, URL-bound historical metadata, never current availability."""
    rows = load(root / 'pending-observations.json', {'observations': []})['observations']
    rows += load(root / 'opportunity-registry.json', {'observation_pool': []})['observation_pool']
    matches = [row for row in rows if row['observation_id'] == observation_id]
    if not matches:
        raise ValueError('Unknown observation identifier')
    allowed = {url for row in matches for url in row.get('evidence_urls', []) + row.get('aliases', [])}
    records = []
    for path in sorted((root / 'page-evidence').glob('*.json')):
        record = load(path, {})
        if not isinstance(record, dict) or record.get('url') not in allowed or not verified(record):
            continue
        fields = {key: record.get(key) for key in ('url', 'title', 'checked_at', 'http_status',
            'content_hash', 'metadata_hash', 'media_types', 'publication_dates', 'date_contexts')}
        fields.update(evidence_file=path.relative_to(root).as_posix(), summary=record.get('summary', record.get('text', ''))[:500],
            historical_snapshot=True, current_availability_verified=False)
        records.append(fields)
    return {'observation_id': observation_id, 'count': len(records), 'records': records}

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--observation-id', action='append', default=[])
    parser.add_argument('--verify-history', type=Path, help='JSON list of URL and short date-quote requests; refetches only in memory')
    parser.add_argument('--history-for-observation', help='Read existing verified subject metadata; does not fetch or prove current availability')
    args = parser.parse_args(argv)
    with locked(args.root):
        recover(args.root)
        if args.history_for_observation:
            result = historical_subject(args.root, args.history_for_observation)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result['count'] else 1
        if args.verify_history:
            requests = load(args.verify_history, None)
            if not isinstance(requests, list) or not requests:
                raise ValueError('Expected a non-empty list of historical date requests')
            verified = [verify_history(item['url'], [item], args.root) for item in requests]
            print(json.dumps(verified, ensure_ascii=False))
            return 0
        pending = load(args.root / 'pending-observations.json', {'observations': []})['observations']
        records = []
        for item in pending:
            if args.observation_id and item['observation_id'] not in args.observation_id: continue
            for url in item['evidence_urls']:
                record = dict(evidence_reference(fetch(url, args.root)), observation_id=item['observation_id']); records.append(record)
        write(args.root / 'review-fetch-index.json', records)
        print(json.dumps({'count': len(records), 'valid': sum(bool(r['valid']) for r in records)}))
        return 0 if all(r['valid'] for r in records) else 1
if __name__ == '__main__': raise SystemExit(main())
