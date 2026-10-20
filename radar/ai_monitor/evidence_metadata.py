"""Small page observations; full HTML and body exist only during collection."""
import re

VERSION = 1
SUMMARY_LIMIT = 500
DATE_TOKEN = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', re.I)


def _digest(value):
    from .queue_store import digest
    return digest(value)


def metadata_valid(record):
    return (record.get('storage_version') == VERSION
            and record.get('metadata_hash') == _digest({k: v for k, v in record.items() if k != 'metadata_hash'}))


def verified(record, expected=None):
    if not isinstance(record, dict) or not record.get('valid') or record.get('http_status') != 200:
        return False
    if record.get('storage_version'):
        good = metadata_valid(record) and record.get('content_verified') is True
        actual = record.get('content_hash')
    else:
        text = record.get('text', '')
        actual = _digest(text)
        good = bool(text) and actual == record.get('content_hash', actual)
    return bool(good and (expected is None or actual == expected))


def history_matches(record, item):
    quote, offset = item.get('original', ''), item.get('text_offset')
    if not quote or not isinstance(offset, int) or offset < 0:
        return False
    if not record.get('storage_version'):
        return record.get('text', '')[offset:offset + len(quote)] == quote
    if not metadata_valid(record):
        return False
    if any(row.get('text_offset') == offset and row.get('quote_hash') == _digest(quote)
           for row in record.get('reviewed_history', [])):
        return True
    for row in record.get('date_contexts', []):
        start = offset - row['text_offset']
        if start >= 0 and row['excerpt'][start:start + len(quote)] == quote:
            return True
    return False


def _dates(rows):
    # Preserve every date and the proof/veto distinction, without copying paragraphs.
    found = {}
    for row in rows:
        key = (row['published_at'], bool(row.get('veto_only')))
        found.setdefault(key, {'published_at': row['published_at'],
                              'locator': row.get('locator', ''),
                              'veto_only': bool(row.get('veto_only'))})
    return list(found.values())


def prepare_history(record, requests):
    """Verify human-selected short quotes against the currently fetched body in memory."""
    from datetime import datetime
    from .eligibility import instant
    if not verified(record):
        raise ValueError('Historical review requires a successful verified page')
    checked = []
    for request in requests:
        quote = request.get('original', '')
        if not isinstance(quote, str) or not 1 <= len(quote) <= 500:
            raise ValueError('Use a short historical date quote, at most 500 characters')
        item = {key: request[key] for key in
                ('original', 'text_offset', 'date_text', 'published_at', 'scope') if key in request}
        if 'text_offset' not in item:
            offset = record['text'].find(quote)
            if offset < 0 or record['text'].find(quote, offset + 1) >= 0:
                raise ValueError('Historical quote must occur exactly once, or supply its exact text_offset')
            item['text_offset'] = offset
        if not history_matches(record, item):
            raise ValueError('Historical quote does not match the live page')
        token = item.get('date_text', '')
        if item.get('scope') != 'same_ai_video_need' or not token or token not in quote:
            raise ValueError('Historical date must identify the same AI video need')
        try:
            parsed = instant(token)
        except ValueError:
            parsed = instant(datetime.strptime(token, '%d %B %Y').isoformat())
        if parsed != instant(item['published_at']):
            raise ValueError('Historical date does not match quote')
        item.update(url=record['url'], evidence_file=record['evidence_file'],
                    content_hash=record['content_hash'], locator='text:' + str(item['text_offset']),
                    veto_only=True)
        checked.append(item)
    return checked


def compact(record, manual_history=()):
    if record.get('storage_version'):
        if not metadata_valid(record):
            raise ValueError('Page metadata integrity mismatch')
        return dict(record)
    from .eligibility import publication_dates, ai_history_dates
    text = record.get('text', '')
    if record.get('valid') and not verified(record):
        raise ValueError('Cannot compact a valid page with a mismatched content hash')
    keys = ('url', 'final_url', 'html_canonical', 'checked_at', 'valid', 'http_status',
            'content_hash', 'raw_html_hash', 'evidence_file', 'media_types', 'status', 'file')
    result = {k: record[k] for k in keys if k in record}
    for key, limit in [('title', 256), ('error', 512)]:
        if key in record:
            result[key] = str(record[key])[:limit]
    result['summary'] = re.sub(r'\s+', ' ', record.get('summary') or text)[:SUMMARY_LIMIT]
    result['content_hash'] = record.get('content_hash', _digest(text))
    result['content_verified'] = verified(record)
    result['publication_dates'] = _dates(publication_dates(record))
    result['ai_history_dates'] = _dates(ai_history_dates(record))
    # Small date-bearing excerpts permit future semantic date review, never a full body.
    contexts = []
    for match in DATE_TOKEN.finditer(text):
        start, end = max(0, match.start() - 112), min(len(text), match.end() + 112)
        if contexts and start <= contexts[-1]['text_offset'] + len(contexts[-1]['excerpt']):
            continue
        contexts.append({'text_offset': start, 'excerpt': text[start:end]})
        if len(contexts) == 128:
            break
    result['date_contexts'] = contexts
    result['reviewed_history'] = []
    for item in manual_history:
        if item.get('content_hash') == result['content_hash'] and history_matches(record, item):
            result['reviewed_history'].append({'text_offset': item['text_offset'],
                                               'quote_hash': _digest(item['original'])})
        else:
            raise ValueError('Cannot preserve unverified manual historical date')
    result['storage_version'] = VERSION
    result['metadata_hash'] = _digest(result)
    return result
