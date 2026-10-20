"""Evidence-backed rolling 48-hour freshness, then cross-site heat ranking."""
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin
from .queue_store import digest, domain, load
from .evidence_metadata import metadata_valid, verified, history_matches

POLICY = 'fresh48h-hot-priority-v2'

def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

class PublicationParser(HTMLParser):
    def __init__(self, urls):
        self.urls = {urldefrag(url)[0].rstrip('/') for url in urls if url}
        super().__init__(); self.dates = []; self.script = False; self.parts = []
    def add(self, value, locator):
        if isinstance(value, str):
            try: instant(value)
            except (ValueError, TypeError): return
            self.dates.append({'published_at': value, 'locator': locator, 'original': value})
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and (attrs.get('property') or attrs.get('name', '')).lower() in {'article:published_time', 'datepublished', 'publishdate', 'pubdate'}:
            self.add(attrs.get('content'), 'meta:' + (attrs.get('property') or attrs.get('name')))
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json': self.script = True; self.parts = []
    def handle_data(self, data):
        if self.script: self.parts.append(data)
    def handle_endtag(self, tag):
        if tag != 'script' or not self.script: return
        self.script = False
        def main_entities(value, path='$'):
            if isinstance(value, list):
                for i, item in enumerate(value): main_entities(item, path + '[' + str(i) + ']')
            elif isinstance(value, dict):
                types = value.get('@type', [])
                if isinstance(types, str): types = [types]
                identifiers = [value.get('url'), value.get('@id'), value.get('mainEntityOfPage')]
                identifiers = [item.get('@id') if isinstance(item, dict) else item for item in identifiers]
                urls = {urldefrag(urljoin(next(iter(self.urls), ''), item))[0].rstrip('/') for item in identifiers if isinstance(item, str)}
                if set(types) & {'Article', 'NewsArticle', 'BlogPosting', 'WebPage', 'CreativeWork', 'VideoObject', 'SoftwareApplication'} and urls & self.urls:
                    self.add(value.get('datePublished'), 'json-ld:' + path + '.datePublished')
                # Only top-level graph entries; never related items/reviews/nested cards.
                if '@graph' in value: main_entities(value['@graph'], path + '.@graph')
        try: main_entities(json.loads(''.join(self.parts)))
        except (ValueError, TypeError): pass


def publication_dates(record):
    if record.get('storage_version'):
        return record.get('publication_dates', []) if metadata_valid(record) else []
    raw = record.get('raw_html')
    if raw and digest(raw) == record.get('raw_html_hash'):
        parser = PublicationParser([record.get(key) for key in ('url', 'final_url', 'html_canonical')]); parser.feed(raw); dates = parser.dates
    else: dates = []
    # Explicit publication labels only; modified dates and copyright are excluded.
    for match in re.finditer(r'(?:Published(?:\s+on)?|发布日期|发布于|发布时间)\s*[:：]?\s*(\d{4}-\d{2}-\d{2}(?:T[0-9:+.Z-]+)?)', record.get('text', ''), re.I):
        try: instant(match[1])
        except ValueError: continue
        dates.append({'published_at': match[1], 'locator': 'text:' + str(match.start()), 'original': match[0], 'veto_only': True})
    return dates


def ai_history_dates(record):
    """Only explicit AI-version/template history; a song/event date is not AI history.

    Conservative sentence-local extraction is a veto only, never freshness proof.
    Reviewers can verify short quotes against a live body before submit for other phrasings.
    """
    if record.get('storage_version'):
        return record.get('ai_history_dates', []) if metadata_valid(record) else []
    text = record.get('text', '')
    rows = []
    cursor, last_boundary = 0, -1
    boundary = re.compile(r'[.!?\n]')
    # Find the short AI phrase first, then its surrounding sentence. Its internal
    # whitespace may cross HTML text-node newlines; other sentence boundaries stay intact.
    for match in re.finditer(r'AI\s+(?:template\s+)?versions?|AI\s+templates?', text, re.I):
        if match.start() < cursor: continue
        for separator in boundary.finditer(text, cursor, match.start()):
            last_boundary = separator.start()
        start = last_boundary + 1
        end = boundary.search(text, match.end())
        cursor = end.start() if end else len(text)
        quote = text[start:cursor]
        last_boundary = cursor
        if not re.search(r'earliest|first|went up|launched|released|published|appeared|available|from|since', quote, re.I): continue
        for date in re.finditer(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', quote, re.I):
            try:
                value = instant(date[0]) if '-' in date[0] else datetime.strptime(date[0], '%d %B %Y').replace(tzinfo=timezone.utc)
            except ValueError: continue
            rows.append({'published_at': value.isoformat(), 'locator': 'text:' + str(start), 'original': quote, 'veto_only': True})
    return rows

def website(url):
    host = domain(url); parts = host.split('.')
    return '.'.join(parts[-3:] if len(parts) > 2 and '.'.join(parts[-2:]) in {'co.uk', 'com.au', 'co.jp'} else parts[-2:])

def qualify(root, need, now):
    reasons = []; dates = []; sites = []; evidence = []
    if need.get('category') != 'video_template': reasons.append('non_video')
    if need.get('known_outside_pool') or need.get('migration_pending'): reasons.append('known_or_migration')
    for row in need.get('valid_evidence', []):
        relative = row.get('evidence_file')
        if not relative: continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()): continue
        record = load(path, {})
        if not row.get('content_hash') or not verified(record, row['content_hash']): continue
        identity = {'site:' + website(url) for url in [record.get('url', row['url']), record.get('final_url'), record.get('html_canonical')] if url}
        if row.get('source_id'): identity.add('source:' + row['source_id'])
        joined = [group for group in sites if group & identity]
        for group in joined: identity.update(group); sites.remove(group)
        sites.append(identity)
        for date in publication_dates(record):
            evidence.append(dict(date, evidence_file=relative, content_hash=row['content_hash'], raw_html_hash=record.get('raw_html_hash')))
            dates.append(instant(date['published_at']))
    current = instant(now)
    cutoff = current - timedelta(hours=48)
    # Discovery timestamps may disprove novelty, never prove it.
    old_signals = [need.get('first_seen'), need.get('first_seen_at')]
    for row in need.get('external_coverage', []) + need.get('valid_evidence', []):
        if isinstance(row, dict):
            old_signals.extend(row.get(key) for key in ('first_seen', 'first_seen_at'))
    historical = []
    for row in need.get('valid_evidence', []):
        relative = row.get('evidence_file')
        if not relative: continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()): continue
        record = load(path, {})
        if not row.get('content_hash') or not verified(record, row['content_hash']): continue
        historical.extend(dict(item, evidence_file=relative, content_hash=row['content_hash']) for item in ai_history_dates(record))
    for item in need.get('historical_demand_evidence', []):
        path = (root / item.get('evidence_file', '')).resolve()
        if not path.is_relative_to(root.resolve()): continue
        record = load(path, {})
        quote = item.get('original', ''); offset = item.get('text_offset')
        if item.get('content_hash') and verified(record, item['content_hash']) and history_matches(record, item):
            historical.append(item)
    # Legacy external coverage dates only veto novelty when the reviewer has
    # explicitly identified their scope as this AI need; never prove freshness.
    for row in need.get('external_coverage', []):
        if isinstance(row, dict) and row.get('scope') == 'same_ai_video_need':
            old_signals.extend(row.get(key) for key in ('published_at', 'datePublished'))
    for row in historical: old_signals.append(row['published_at'])
    known_old = bool(need.get('known_outside_pool'))
    for value in old_signals:
        if not value: continue
        try:
            if instant(value) < cutoff: known_old = True
        except (ValueError, TypeError): pass
    if known_old: reasons.append('known_historical_demand')
    confirmed = [instant(row['published_at']) for row in evidence if not row.get('veto_only')]
    if not confirmed: reasons.append('unknown_publication_date')
    if dates and min(dates) < cutoff: reasons.append('older_than_48_hours')
    if any(date > current for date in dates): reasons.append('future_publication_date')
    fresh = bool(confirmed) and min(dates) >= cutoff and max(dates) <= current and not known_old
    tier = 1 if fresh else (2 if len(sites) >= 2 else 3)
    blocked = need.get('category') != 'video_template' or need.get('migration_pending') or not sites
    return {'policy_version': POLICY, 'eligible': not bool(blocked), 'priority_tier': tier, 'fresh_48h': fresh, 'hot': len(sites) >= 2, 'reasons': reasons, 'source_count': len(sites), 'earliest_known_published_at': min(dates).isoformat() if dates else None, 'publication_evidence': evidence, 'historical_demand_evidence': historical, 'evaluated_at': now}
