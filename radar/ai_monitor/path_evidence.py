"""Opt-in URL/catalog names: a discovery basis, never a verified page body."""
import re
from datetime import datetime
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo

from .queue_store import digest, load, monitor, write

GENERIC = set('ai video videos image images photo photos template templates generator generate maker '
              'create free online tool tools app apps motion effects effect blog resource resources '
              'index home details detail preview category categories new trending trend edit popular latest'.split())
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)


def policy(root, at=None):
    value = load(root / 'monitor/config.json', {}).get('review_workflow', {})
    start = value.get('path_first_from')
    if not start:
        return None
    effective = datetime.fromisoformat(start).date()
    instant = datetime.fromisoformat((at or monitor.utc_now()).replace('Z', '+00:00'))
    if instant.tzinfo is None:
        raise ValueError('Path-first policy requires an aware instant')
    return start if instant.astimezone(ZoneInfo('Asia/Shanghai')).date() >= effective else None


def path_name(url):
    parts = [part for part in unquote(urlsplit(url).path).split('/') if part]
    if not parts:
        return None
    slug = re.sub(r'\.(?:html?|php|aspx?)$', '', parts[-1], flags=re.I)
    if UUID.fullmatch(slug) or re.fullmatch(r'[0-9a-f]{16,}|\d+', slug, re.I):
        return None
    # Pippit includes a readable slug before its numeric entity ID.
    slug = re.sub(r'-\d{15,}$', '', slug)
    if re.fullmatch(r'[A-Za-z0-9]{20,}', slug):
        return None
    name = re.sub(r'[-_\s]+', ' ', slug).strip()
    words = re.findall(r'[^\W_]+', name, re.UNICODE)
    if not words or not any(word.casefold() not in GENERIC for word in words):
        return None
    return name[:256]


def observation_for(root, url):
    pending = load(root / 'pending-observations.json', {'observations': []})['observations']
    return next((row for row in pending if url in row.get('evidence_urls', []) + row.get('aliases', [])), {})


def catalog_name(root, url, observation):
    key = observation.get('template_entity_id')
    if not key:
        return None
    entity = load(root / 'video-template-candidates.json', {'entities': {}})['entities'].get(key, {})
    name = entity.get('name', '').strip()
    if (not name or UUID.fullmatch(name) or name.isdigit() or url not in entity.get('urls', [])
            or entity.get('named_status') == 'generic_or_unclear'):
        return None
    return {field: entity.get(field) for field in ('entity_id', 'name', 'fingerprint', 'updated_at', 'urls')}


def create(root, url, observation=None, *, source_id=None, observed_at=None, supply_at=None):
    at = observed_at or monitor.utc_now()
    effective = policy(root, at)
    if not effective:
        return None
    observation = observation if observation is not None else observation_for(root, url)
    source_id = source_id or observation.get('source_id')
    if not source_id:
        return None
    name = path_name(url)
    snapshot = None if name else catalog_name(root, url, observation)
    if not name and not snapshot:
        return None
    name = name or snapshot['name']
    if (supply_at is None and observation.get('is_first_seen')
            and observation.get('template_change_type') == 'new_entity'):
        supply_at = observation.get('observed_at')
    result = {'record_type': 'discovery_name', 'storage_version': 1,
              'path_first_from': effective, 'url': url, 'source_id': source_id,
              'checked_at': at, 'valid': True, 'http_status': None,
              'page_content_fetched': False, 'content_verified': False,
              'name_origin': 'url_path' if snapshot is None else 'template_catalog',
              'title': name, 'summary': 'URL/catalog name: ' + name,
              'publication_dates': [], 'ai_history_dates': [], 'date_contexts': [],
              'media_types': [], 'supply_added_at': supply_at}
    if snapshot:
        result['catalog_snapshot'] = snapshot
    result['content_hash'] = digest({'url': url, 'name': name, 'origin': result['name_origin']})
    result['evidence_file'] = 'page-evidence/' + digest(result) + '.json'
    result['metadata_hash'] = digest(result)
    write(root / result['evidence_file'], result)
    return result


def verified(root, record, expected=None):
    from .evidence_metadata import verified as page_verified, metadata_valid
    if page_verified(record, expected):
        return True
    if (not isinstance(record, dict) or record.get('record_type') != 'discovery_name'
            or record.get('page_content_fetched') is not False
            or record.get('content_verified') is not False or record.get('http_status') is not None
            or not record.get('path_first_from')
            or not record.get('source_id') or not metadata_valid(record)
            or policy(root, record.get('checked_at')) != record.get('path_first_from')):
        return False
    if record.get('name_origin') == 'url_path':
        named = path_name(record.get('url', '')) == record.get('title')
    elif record.get('name_origin') == 'template_catalog':
        snapshot = record.get('catalog_snapshot', {})
        named = bool(snapshot.get('entity_id') and snapshot.get('name') == record.get('title')
                     and record.get('url') in snapshot.get('urls', []))
    else:
        return False
    actual = digest({'url': record['url'], 'name': record['title'], 'origin': record['name_origin']})
    return bool(named and record.get('content_hash') == actual and (expected is None or actual == expected))


def collector(root, source, url, result):
    added = [row for row in result.get('added', []) if row['url'] == url]
    at = result['observed_at']
    is_new = (result.get('baseline_created') is False and not result.get('scope_reset')
              and added and all(row.get('first_seen') == at for row in added))
    return create(root, url, source_id=source['id'], observed_at=at, supply_at=at if is_new else None)


def groups(items):
    buckets = {}
    for row in items:
        name = path_name(row['canonical_path'])
        key = row.get('need_id') or (name.casefold() if name else row['observation_id'])
        group = buckets.setdefault(key, {'candidate_name': name, 'members': [], 'semantic_confirmation_required': True})
        group['members'].append(row['observation_id'])
    return list(buckets.values())
