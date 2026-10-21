"""Named video templates: single entities, UUID pages and aggregate card changes.

First snapshots are inventory. Discovery, demand and generation remain distinct.
Only compact metadata and fingerprints are persisted, never HTML or page bodies.
"""
import argparse
from html.parser import HTMLParser
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urldefrag

from . import run_monitor as monitor, review_pages
from .queue_store import load, locked, recover, commit, op, digest, canonical_path, observation_id, merge_observations, earliest_time
from .evidence_metadata import compact
from .runtime import DEFAULT_ROOT
from .scheduled_scan import batch_lock

HOST = 'https://higgsfield.ai'
MAPS = {
    'core': ['motion/sitemap.xml', 'effects/sitemap.xml', 'apps/sitemap.xml'],
    'daily': ['sitemap-marketing.xml', 'blog/sitemap.xml', 'creator-hub/sitemap.xml',
              'marketing-studio/sitemap.xml', 'mixed-media-presets/sitemap.xml'],
    'weekly': ['sitemap-projects.xml', 'academy/sitemap.xml',
               'enterprise-help-center/sitemap.xml', 'contests/sitemap.xml', 'original-series/sitemap.xml'],
}
AGGREGATES = ['/apps', '/effects', '/creator-hub/changelog', '/genjutsu']
ALLOW_EMPTY_FEEDS = {'contests/sitemap.xml'}
SUPPLEMENTAL_PAGES = {'/genjutsu'}
TEMPLATE_PATH = re.compile(r'^/(?:motion|effects|apps)/[^/?]+')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)


def clean_name(value):
    value = re.sub(r'\s+', ' ', value).strip()
    value = re.split(r'\s+[|–—]\s+', value)[0].strip()
    return value if value and not UUID.fullmatch(value) else ''


class Cards(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.cards, self.h1, self.structured = base, [], [], []
        self.anchor = None
        self.in_h1 = False
        self.script = None
        self.skipped = []
        self.references = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('nav', 'footer', 'header'):
            self.skipped.append(tag)
        if tag == 'script':
            self.script = [] if attrs.get('type') == 'application/ld+json' else None
        if self.skipped:
            return
        if tag in ('video', 'source') and attrs.get('src'):
            self.references.append(urljoin(self.base, attrs['src']))
        if tag == 'h1':
            self.in_h1 = True
        if tag == 'a':
            url = urldefrag(urljoin(self.base, attrs.get('href', '')))[0]
            self.anchor = {'url': url, 'parts': [], 'media': []} if urlsplit(url).netloc == urlsplit(self.base).netloc and TEMPLATE_PATH.match(urlsplit(url).path) else None
            if self.anchor:
                self.anchor['parts'].append(attrs.get('aria-label') or attrs.get('title') or '')
        if self.anchor and tag == 'img':
            self.anchor['parts'].append(attrs.get('alt', ''))
        if self.anchor and tag in ('video', 'source') and attrs.get('src'):
            self.anchor['media'].append(urljoin(self.base, attrs['src']))

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.structured.append(json.loads(''.join(self.script)))
            except (ValueError, TypeError):
                pass
            self.script = None
        if tag in self.skipped:
            self.skipped.remove(tag)
        if tag == 'h1':
            self.in_h1 = False
        if tag == 'a' and self.anchor:
            name = clean_name(' '.join(dict.fromkeys(p for p in self.anchor['parts'] if p)))
            if name:
                self.cards.append({'url': self.anchor['url'], 'name': name,
                                   'reference_hash': digest(sorted(set(self.anchor['media']))) if self.anchor['media'] else ''})
            self.anchor = None

    def handle_data(self, value):
        if self.script is not None:
            self.script.append(value)
        if not self.skipped:
            if self.in_h1:
                self.h1.append(value)
            if self.anchor:
                self.anchor['parts'].append(value.strip())


def structured_name(value):
    if isinstance(value, dict):
        if value.get('@type') in ('VideoObject', 'SoftwareApplication', 'Product') and value.get('name'):
            return clean_name(str(value['name']))
        for key in ('mainEntity', '@graph'):
            found = structured_name(value.get(key))
            if found:
                return found
    if isinstance(value, list):
        return next((name for item in value if (name := structured_name(item))), '')
    return ''


def extract(record):
    parser = Cards(record.get('final_url') or record['url'])
    parser.feed(record.get('raw_html', ''))
    name = clean_name(' '.join(parser.h1)) or structured_name(parser.structured) or clean_name(record.get('title', ''))
    return name, parser.cards


def identity(url):
    path = canonical_path(url, {'id': 'higgsfield-ai'}).rstrip('/')
    match = UUID.search(path)
    return 'higgsfield-ai:' + (match.group(0).lower() if match else path)


def handoff(name, summary, url):
    context = name + ' ' + summary
    lane = 'video_template'
    if re.search(r'\b(api|sdk|model|pricing|billing|login|refund)\b', context, re.I):
        lane = 'model_or_developer' if re.search(r'\b(model|api|sdk)\b', context, re.I) else 'excluded_page_purpose'
    elif re.search(r'\b(photo|image|poster|product photography)\b', context, re.I) and not re.search(r'\b(video|dance|motion|animation|freestyle)\b', context, re.I):
        lane = 'static_image'
    return {'category': lane, 'discovery': 'first_discovered', 'publication': 'unverified',
            'production_intent': 'unverified', 'independent_demand': 'unverified',
            'implementation': {'base': '024', 'reference': '025 fixed action and BGM only',
                               'generation_validation': 'not_tested', 'launch_ready': False,
                               'reference_asset_rights': 'unverified',
                               'fixed_reference_contract': '1/2 photos; 10s 720x1280 H.264 MP4 <=20000000 bytes; Yir Mini; output duration separate'},
            'next_action': 'verify named video result, creation intent, independent demand and capability',
            'source_url': url}


def run(root, tier='core', fetcher=monitor.fetch_bytes, page_reader=review_pages.read_page, snapshot=None):
    root = Path(root).resolve()
    with batch_lock(root), locked(root):
        recover(root)
        state = load(root / 'template-state.json', {'schema_version': 1, 'feeds': {}, 'aggregates': {}})
        pool = load(root / 'video-template-candidates.json', {'schema_version': 1, 'entities': {}})
        pending = load(root / 'pending-observations.json', {'observations': []})
        legacy = load(root / 'monitor/state/higgsfield-ai.json', {}).get('seen', {})
        legacy_entities = {}
        for url, seen in legacy.items():
            key = identity(url)
            legacy_entities[key] = earliest_time(legacy_entities.get(key), seen)
        incoming = []
        timestamp = monitor.utc_now()
        report = {'observed_at': timestamp, 'tier': tier, 'status': 'ok', 'feeds': [], 'aggregates': [], 'events': [],
                  'notice': 'First inventory is historical; discovery is not publication, demand or generation validation.'}
        options = load(root / 'monitor/config.json', {}).get('fetch', {})

        def candidate(url, name, summary='', reference_hash='', change='new_entity', inventory=False, lastmod=None):
            key = identity(url)
            old = pool['entities'].get(key)
            # Missing video markup is unknown, never proof of changed reference.
            reference_hash = reference_hash or (old or {}).get('reference_hash', '')
            normalized_name = re.sub(r'[\s_-]+', '', name).casefold()
            fingerprint = digest({'name': normalized_name, 'reference_hash': reference_hash})
            if old and old['fingerprint'] == fingerprint:
                old['aliases'] = sorted(set(old.get('aliases', []) + [name]))
                old['urls'] = sorted(set(old.get('urls', []) + [url]))
                return
            first_seen = old['first_seen_at'] if old else earliest_time(legacy_entities.get(key), timestamp)
            change = ('name_update' if old['name'] != name else 'reference_update') if old else change
            row = {'entity_id': key, 'name': name, 'aliases': sorted(set((old or {}).get('aliases', []) + [name])),
                   'urls': sorted(set((old or {}).get('urls', []) + [url])), 'first_seen_at': first_seen,
                   'source_lastmod': lastmod, 'oldest_external_evidence': (old or {}).get('oldest_external_evidence'),
                   'change_type': 'historical_inventory' if inventory and not old else change,
                   'fingerprint': fingerprint, 'summary': summary[:500], 'reference_hash': reference_hash,
                   'source_group': 'higgsfield-ai', 'entity_relation': 'UUID/locale identity; other semantic aliases require review need_id merge',
                   'handoff': handoff(name, summary, url), 'updated_at': timestamp}
            pool['entities'][key] = row
            if not inventory:
                event_key = digest({'entity': key, 'fingerprint': fingerprint})
                events = (old or {}).get('event_keys', [])
                if event_key not in events:
                    report['events'].append({'entity_id': key, 'name': name, 'change_type': change, 'event_key': event_key})
                    events = events + [event_key]
                    path = canonical_path(url, {'id': 'higgsfield-ai'})
                    incoming.append({'observation_id': observation_id('higgsfield-ai', path),
                                     'source_id': 'higgsfield-ai', 'source_name': 'Higgsfield',
                                     'canonical_path': path, 'observed_at': timestamp, 'first_seen': first_seen,
                                     'evidence_urls': [url], 'aliases': [url], 'evidence': [],
                                     'is_first_seen': not bool(old or legacy.get(url)), 'classification': 'unreviewed',
                                     'review_lane': row['handoff']['category'], 'review_status': 'pending',
                                     'template_entity_id': key, 'template_change_type': change})
                row['event_keys'] = events
            else:
                row['event_keys'] = (old or {}).get('event_keys', [])

        tiers = ['core', 'daily', 'weekly'][:['core', 'daily', 'weekly'].index(tier) + 1]
        for route in [route for level in tiers for route in MAPS[level]]:
            row = {'url': HOST + '/' + route}
            report['feeds'].append(row)
            try:
                raw = fetcher(row['url'], options)
                try:
                    _, entries = monitor.parse_sitemap(raw)
                except ValueError as error:
                    # The known contests feed is legitimately empty. Retain the
                    # strict anomaly protection for every other feed and every
                    # malformed/DTD sitemap, including this route.
                    if route not in ALLOW_EMPTY_FEEDS or str(error) != 'Empty sitemap is anomalous; previous baseline retained':
                        raise
                    entries = []
                current = {entry['url']: entry.get('lastmod') for entry in entries}
                old = state['feeds'].get(route)
                # First snapshot is inventory only; do not fetch hundreds of old
                # UUID detail pages or manufacture first-run publication events.
                changed = [] if old is None else [url for url in current if url not in old['urls'] or old['urls'][url] != current[url]]
                errors = []
                for url in changed:
                    record = page_reader(url)
                    if not record.get('valid'):
                        errors.append({'url': url, 'error': record.get('error', 'page unread')})
                        continue
                    name, _ = extract(record)
                    if not TEMPLATE_PATH.match(canonical_path(url, {'id': 'higgsfield-ai'})):
                        # Supplemental articles, changelog and style presets enter
                        # the same human review queue without pretending to be a
                        # named video template or an independently confirmed need.
                        path = canonical_path(url, {'id': 'higgsfield-ai'})
                        relative = 'page-evidence/' + digest(record) + '.json'
                        record['evidence_file'] = relative
                        metadata = compact(record)
                        from .queue_store import write, evidence_reference
                        write(root / relative, metadata)
                        incoming.append({'observation_id': observation_id('higgsfield-ai', path),
                                         'source_id': 'higgsfield-ai', 'source_name': 'Higgsfield',
                                         'canonical_path': path, 'observed_at': timestamp,
                                         'first_seen': earliest_time(legacy.get(url), timestamp),
                                         'evidence_urls': [url], 'aliases': [url], 'evidence': [evidence_reference(metadata)],
                                         'is_first_seen': not bool(legacy.get(url)), 'classification': 'unreviewed',
                                         'review_lane': 'supplemental_evidence', 'review_status': 'pending'})
                        continue
                    if not name or UUID.fullmatch(name):
                        errors.append({'url': url, 'error': 'template name unread'})
                        continue
                    parsed = Cards(record.get('final_url') or url)
                    parsed.feed(record.get('raw_html', ''))
                    reference = digest(sorted(set(parsed.references))) if parsed.references else ''
                    candidate(url, name, record.get('summary', ''), reference,
                              inventory=old is None, lastmod=current[url])
                # A failed detail must be retried, not hidden behind a successful baseline.
                accepted = dict(current)
                for error in errors:
                    accepted.pop(error['url'], None)
                    if old and error['url'] in old['urls']:
                        accepted[error['url']] = old['urls'][error['url']]
                state['feeds'][route] = {'urls': accepted, 'observed_at': timestamp}
                row.update(status='partial' if errors else 'ok', url_count=len(current), errors=errors,
                           baseline_created=old is None)
            except Exception as error:
                row.update(status='error', error=type(error).__name__ + ': ' + str(error))
        for route in AGGREGATES if tier != 'core' else AGGREGATES[:2]:
            row = {'url': HOST + route}
            report['aggregates'].append(row)
            try:
                record = page_reader(row['url'])
                if not record.get('valid'):
                    raise ValueError(record.get('error', 'aggregate unread'))
                _, cards = extract(record)
                if not cards:
                    if route in SUPPLEMENTAL_PAGES:
                        name, _ = extract(record)
                        fingerprint = digest({'name': name, 'summary': record.get('summary', '')[:500]})
                        old = state['aggregates'].get(route)
                        state['aggregates'][route] = {'cards': {}, 'page_fingerprint': fingerprint, 'observed_at': timestamp}
                        row.update(status='ok', role='supplemental_page', catalog_available=False,
                                   page_changed=bool(old and old.get('page_fingerprint') != fingerprint), baseline_created=old is None)
                        if row['page_changed']:
                            from .queue_store import write, evidence_reference
                            relative = 'page-evidence/' + digest(record) + '.json'
                            record['evidence_file'] = relative
                            metadata = compact(record)
                            write(root / relative, metadata)
                            path = canonical_path(row['url'], {'id': 'higgsfield-ai'})
                            incoming.append({'observation_id': observation_id('higgsfield-ai', path),
                                             'source_id': 'higgsfield-ai', 'source_name': 'Higgsfield',
                                             'canonical_path': path, 'observed_at': timestamp,
                                             'first_seen': earliest_time(legacy.get(row['url']), old['observed_at']),
                                             'evidence_urls': [row['url']], 'aliases': [row['url']],
                                             'evidence': [evidence_reference(metadata)], 'is_first_seen': False,
                                             'classification': 'unreviewed', 'review_lane': 'developer_update', 'review_status': 'pending'})
                        continue
                    raise ValueError('No readable named template cards; dynamic catalog not verified')
                old = state['aggregates'].get(route)
                current = {identity(card['url']): digest(card) for card in cards}
                for card in cards:
                    if old is None or old['cards'].get(identity(card['url'])) != digest(card):
                        candidate(card['url'], card['name'], reference_hash=card['reference_hash'], inventory=old is None)
                state['aggregates'][route] = {'cards': current, 'observed_at': timestamp}
                row.update(status='ok', card_count=len(cards), baseline_created=old is None)
            except Exception as error:
                row.update(status='unread', error=type(error).__name__ + ': ' + str(error))
        report['status'] = 'ok' if all(r['status'] == 'ok' for r in report['feeds'] + report['aggregates']) else 'partial'
        report['candidate_count'] = len(pool['entities'])
        pending['observations'] = merge_observations(pending['observations'], incoming)
        report['queued_observation_count'] = len(incoming)
        commit(root, 'template_scan', [op('template-state.json', state), op('video-template-candidates.json', pool),
                                      op('pending-observations.json', pending), op('latest-template-scan.json', report)])
        if snapshot is not None:
            snapshot.update(report)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report['status'] == 'ok' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--tier', choices=['core', 'daily', 'weekly'], default='core')
    args = parser.parse_args(argv)
    from .scheduled_scan import ScanBusy
    try:
        return run(args.root, args.tier)
    except ScanBusy as error:
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
