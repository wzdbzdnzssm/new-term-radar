"""Pippit's scoped English articles and three explicit AI video trend catalogs.

Only the selected SSR video array is evidence. Catalog placement, uploads and
platform counters do not establish publication recency or independent demand.
"""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

from . import review_pages
from .queue_store import (canonical_path, commit, digest, load, locked,
                          merge_observations, observation_id, op, recover)
from .runtime import DEFAULT_ROOT
from .scheduled_scan import ScanBusy, batch_lock
from .templates import handoff

HOST = 'https://www.pippit.ai'
SOURCE_ID = 'pippit-ai'
ARTICLE_SOURCE = {
    'id': SOURCE_ID, 'name': 'Pippit', 'enabled': True,
    'sitemap_urls': [HOST + '/sitemap/new/573081/default/index0.xml'],
    'include_path_regex': [r'^/(?:blog|resource|tools|create)(?:/|$)'],
    'exclude_path_regex': [], 'max_sitemaps': 1,
}
PAGES = [HOST + '/templates/' + route for route in (
    'trending-ai-new-templates-2026',
    'ai-generated-new-trending-video',
    'free-ai-video-templates-trending',
)]
WEB_ID = re.compile(r'\d{19}\Z')


class RouterData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.payloads = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('id') == '__MODERN_ROUTER_DATA__':
            self.active = True
            self.parts = []

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.payloads.append(''.join(self.parts))
            self.active = False


def generic_name(name):
    text = re.sub(r'[^\w\s]', ' ', name.casefold())
    words = text.split()
    # These observed source labels describe an edit/ranking, not a named
    # playground. Match combinations without rejecting ordinary concrete names.
    generic = {'new', 'latest', 'viral', 'vairal', 'trend', 'trending', 'top',
               'edit', 'edits', 'template', 'templates', 'video', 'videos',
               'ai', 'vlog', 'photo', 'cinematic', 'dump'}
    return not words or all(word in generic or re.fullmatch(r'20\d{2}', word) for word in words)


def parse_cards(html):
    parser = RouterData()
    parser.feed(html)
    if len(parser.payloads) != 1:
        raise ValueError('Expected exactly one modern router SSR payload')
    try:
        data = json.loads(parser.payloads[0])
        videos = data['loaderData']['templates_(keyword)/page']['project']['kep']['templates']['video']
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('Selected SSR video catalog is unavailable or invalid') from error
    if not isinstance(videos, list) or not videos:
        raise ValueError('Selected SSR video catalog is empty; previous snapshot preserved')
    cards = {}
    for item in videos:
        if not isinstance(item, dict):
            raise ValueError('Invalid SSR video item')
        identifier, name, slug = (item.get(key) for key in ('web_id', 'short_title', 'short_title_en'))
        duration, media = item.get('duration'), item.get('video_url')
        if (not isinstance(identifier, str) or not WEB_ID.fullmatch(identifier)
                or not isinstance(name, str) or not name.strip()
                or not isinstance(slug, str) or not slug.strip()
                or isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0
                or not isinstance(media, str) or urlsplit(media).scheme not in ('https', 'http')
                or not urlsplit(media).netloc):
            raise ValueError('Video item lacks an exact string ID, title, duration or media URL')
        name = re.sub(r'\s+', ' ', name).strip()
        card = {'web_id': identifier, 'name': name,
                'url': HOST + '/template-detail/' + quote(slug.strip(), safe='-') + '-' + identifier,
                'summary': str(item.get('title') or '')[:500],
                'source_duration_ms': duration, 'source_uploaded_at': item.get('upload_date'),
                # Signed query strings and CDN host rotations are not asset changes.
                # Do not persist signed media URLs or download the referenced video.
                'media_path': urlsplit(media).path,
                'platform_metrics': {key: item.get(key) for key in ('usage_amount', 'play_amount')},
                'named_status': 'generic_or_unclear' if generic_name(name) else 'named_candidate'}
        old = cards.get(identifier)
        if old and fingerprint(old) != fingerprint(card):
            raise ValueError('Conflicting duplicate entity in one SSR catalog')
        cards[identifier] = card
    return list(cards.values())


def fingerprint(card):
    return digest({key: card[key] for key in ('web_id', 'name', 'media_path', 'source_duration_ms')})


def configured(root):
    return any(source.get('id') == SOURCE_ID and source.get('enabled')
               for source in load(Path(root) / 'monitor/config.json', {}).get('sources', []))


def run(root, page_reader=review_pages.read_page, snapshot=None):
    root = Path(root).resolve()
    with batch_lock(root), locked(root):
        recover(root)
        result = run_locked(root, page_reader)
        if snapshot is not None:
            snapshot.update(load(root / 'latest-pippit-template-scan.json', {}))
        return result


def run_locked(root, page_reader):
    """Caller owns both existing scan and queue locks; no external delivery."""
    from .run_monitor import utc_now
    state = load(root / 'pippit-template-state.json', {'schema_version': 1, 'pages': {}, 'seen_ids': {}})
    pool = load(root / 'video-template-candidates.json', {'schema_version': 1, 'entities': {}})
    pending = load(root / 'pending-observations.json', {'observations': []})
    timestamp = utc_now()
    report = {'source_id': SOURCE_ID, 'observed_at': timestamp, 'status': 'ok', 'pages': [], 'events': [],
              'scope': 'Three selected AI video trend pages; not the 335-shard template library',
              'notice': 'First inventory and source upload dates are not new demand; platform usage/play counters are not independent demand.'}
    incoming, accepted_ids, supply_deltas = [], set(), []
    for url in PAGES:
        row = {'url': url}
        report['pages'].append(row)
        try:
            failures = []
            for attempt in range(2):
                try:
                    record = page_reader(url)
                    if not record.get('valid'):
                        raise ValueError(record.get('error') or
                            'Page unavailable (HTTP ' + str(record.get('http_status', 'unknown')) +
                            ', visible characters ' + str(len(record.get('text', ''))) + ')')
                    cards = parse_cards(record.get('raw_html', ''))
                    break
                except MemoryError:
                    raise
                except Exception as error:
                    failures.append(type(error).__name__ + ': ' + str(error))
                    if attempt:
                        raise
            row['read_attempts'] = attempt + 1
            if failures:
                row['read_errors'] = failures
        except Exception as error:
            row.update(read_attempts=len(failures), read_errors=failures)
            row.update(status='unread', error=type(error).__name__ + ': ' + str(error))
            continue
        previous = state['pages'].get(url)
        added_urls = []
        for card in cards:
            identifier = card['web_id']
            key = SOURCE_ID + ':' + identifier
            old = pool['entities'].get(key)
            first_seen = (old or {}).get('first_seen_at') or state['seen_ids'].get(identifier) or timestamp
            seen = identifier in state['seen_ids']
            initial = previous is None
            decision = handoff(card['name'], card['summary'], card['url'])
            decision.update(independent_demand='unverified', production_intent='unverified', publication='unverified')
            decision['discovery'] = (old or {}).get('handoff', {}).get('discovery', 'historical_inventory' if initial else 'first_discovered')
            if card['named_status'] == 'generic_or_unclear':
                decision['category'] = 'generic_video_edit'
            entity = dict(old or {})
            current_fingerprint = fingerprint(card)
            change_type = ('historical_inventory' if initial else 'new_entity') if not old else (
                old.get('change_type', 'observed') if old.get('fingerprint') == current_fingerprint else
                ('name_update' if old['name'] != card['name'] else 'reference_update'))
            entity.update(entity_id=key, name=card['name'], source_group=SOURCE_ID,
                          aliases=sorted(set((old or {}).get('aliases', []) + [card['name']])),
                          urls=sorted(set((old or {}).get('urls', []) + [card['url']])),
                          first_seen_at=first_seen, updated_at=timestamp,
                          change_type=change_type, fingerprint=current_fingerprint, summary=card['summary'],
                          source_uploaded_at=card['source_uploaded_at'], upload_date_is_demand_origin=False,
                          source_duration_ms=card['source_duration_ms'], platform_metrics=card['platform_metrics'],
                          platform_metrics_are_independent_demand=False,
                          named_status=card['named_status'], handoff=decision,
                          catalog_urls=sorted(set((old or {}).get('catalog_urls', []) + [url])),
                          event_keys=list((old or {}).get('event_keys', [])))
            event_key = digest(SOURCE_ID + ':' + identifier + ':named_discovery')
            became_named = bool(old and old.get('named_status') == 'generic_or_unclear' and card['named_status'] == 'named_candidate')
            if not initial and card['named_status'] == 'named_candidate' and (not seen or became_named) and event_key not in entity['event_keys']:
                change = 'named_template_identified' if became_named else 'new_entity'
                report['events'].append({'entity_id': key, 'name': card['name'], 'change_type': change, 'event_key': event_key})
                entity['event_keys'].append(event_key)
                entity['change_type'] = change
                if not seen and not old:
                    added_urls.append(card['url'])
                path = canonical_path(card['url'], {'id': SOURCE_ID})
                incoming.append({'observation_id': observation_id(SOURCE_ID, path), 'source_id': SOURCE_ID,
                                 'source_name': 'Pippit', 'canonical_path': path, 'observed_at': timestamp,
                                 'first_seen': first_seen, 'evidence_urls': entity['urls'], 'aliases': entity['urls'],
                                 'evidence': [], 'evidence_errors': [], 'is_first_seen': not seen,
                                 'classification': 'unreviewed', 'review_lane': 'video_candidate',
                                 'review_status': 'pending', 'template_entity_id': key, 'template_change_type': change})
            pool['entities'][key] = entity
            state['seen_ids'].setdefault(identifier, first_seen)
            accepted_ids.add(identifier)
        state['pages'][url] = {'observed_at': timestamp, 'ids': sorted(card['web_id'] for card in cards)}
        row.update(status='ok', card_count=len(cards), baseline_created=previous is None,
                   raw_html_hash=record.get('raw_html_hash'), named_candidate_count=sum(card['named_status'] == 'named_candidate' for card in cards))
        if added_urls:
            delta = {'source_id':SOURCE_ID,'index_url':url,'status':'ok','baseline_created':False,
                     'before_observed_at':previous['observed_at'],'observed_at':timestamp,
                     'added_urls':sorted(set(added_urls)),
                     'notice':'New named template ID after established SSR baseline; not demand birth or search heat.'}
            relative = 'supply-deltas/' + digest(delta) + '.json'
            supply_deltas.append(op(relative,delta))
            row['supply_delta'] = {'path':relative,'sha256':digest(delta)}
    report['status'] = 'ok' if all(row['status'] == 'ok' for row in report['pages']) else 'partial'
    report.update(unique_video_count=len(accepted_ids), queued_observation_count=len(incoming),
                  retained_entity_count=sum(key.startswith(SOURCE_ID + ':') for key in pool['entities']))
    # A fully failed read preserves both the snapshots and shared queues byte for
    # byte; its explicit degraded report must still be visible to daily review.
    operations = [op('latest-pippit-template-scan.json', report)] + supply_deltas
    if accepted_ids:
        operations += [op('pippit-template-state.json', state), op('video-template-candidates.json', pool)]
    if incoming:
        pending['observations'] = merge_observations(pending['observations'], incoming)
        operations.append(op('pending-observations.json', pending))
    commit(root, 'pippit_template_scan', operations)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'ok' else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        return run(args.root)
    except ScanBusy as error:
        print(json.dumps({'status': 'busy', 'error': str(error)}))
        return 75


if __name__ == '__main__':
    raise SystemExit(main())
