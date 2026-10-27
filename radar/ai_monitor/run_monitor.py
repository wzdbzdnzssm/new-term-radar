"""Observe public sitemap URL changes; requires Python 3.9+, standard library only."""
import argparse
import gzip
import hashlib
import io
import ipaddress
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

MAX_DEPTH = 20
MAX_RETRY_WAIT = 30


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def checked_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are accepted")
    # Redirects and sitemap children receive the same URL validation.
    host = (parsed.hostname or '').lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError("Local addresses are not public sitemap sources")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("Private addresses are not public sitemap sources")
    return value


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def inflate(data, max_bytes):
    if data[:2] == b"\x1f\x8b":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Sitemap exceeds max_bytes (including decompressed bytes)")
    return data


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def parse_sitemap(data, max_bytes=15000000, allow_empty=False):
    """Only direct sitemap/url children and their direct loc/lastmod count."""
    data = inflate(data, max_bytes)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("DTD/entity declarations are not supported")
    root = ET.fromstring(data)
    kind = local_name(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise ValueError("Expected urlset or sitemapindex XML")
    root_ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
    entry_tag = root_ns + ("url" if kind == "urlset" else "sitemap")
    records = []
    for entry in root:
        if entry.tag != entry_tag:
            continue
        locations = [e.text.strip() for e in entry if e.tag == root_ns + "loc" and e.text and e.text.strip()]
        if len(locations) != 1:
            raise ValueError("Each sitemap/url entry must have exactly one direct loc")
        mods = [e.text.strip() for e in entry if e.tag == root_ns + "lastmod" and e.text and e.text.strip()]
        if len(mods) > 1:
            raise ValueError("Duplicate direct lastmod")
        records.append({"url": checked_url(locations[0]), "lastmod": mods[0] if mods else None})
    if not records and not (allow_empty and kind == 'urlset'):
        raise ValueError("Empty sitemap is anomalous; previous baseline retained")
    return kind, records


def retry_seconds(headers):
    value = headers.get("Retry-After") if headers else None
    if not value:
        return 1.0
    try:
        delay = float(value)
    except ValueError:
        delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delay)


def fetch_bytes(url, options):
    checked_url(url)
    opener = urllib.request.build_opener(CheckedRedirect())
    limit = int(options.get("max_bytes", 15000000))
    timeout = float(options.get("timeout_seconds", 25))
    delay = float(options.get("request_delay_seconds", 0.25))
    for attempt in range(2):
        if delay:
            time.sleep(delay)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "SitemapTrendMonitor/1.0", "Accept": "application/xml,text/xml,*/*", "Accept-Encoding": "gzip"})
            with opener.open(req, timeout=timeout) as response:
                checked_url(response.geturl())
                data = response.read(limit + 1)
                return inflate(data, limit)
        except urllib.error.HTTPError as error:
            if attempt:
                raise
            if error.code == 429:
                wait = retry_seconds(error.headers)
                if wait > MAX_RETRY_WAIT:
                    raise ValueError("429 Retry-After exceeds bounded retry wait; baseline retained") from error
                time.sleep(wait)
            else:
                time.sleep(1)
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt:
                raise
            time.sleep(1)
    raise RuntimeError("Request failed")


class CrawlFailure(Exception):
    def __init__(self, status, detail, checked_maps, observed_count):
        super().__init__(detail)
        self.status = status
        self.checked_maps = checked_maps
        self.observed_count = observed_count


def _crawl(source, options, fetcher=fetch_bytes, previous=None, declared=()):
    includes = [re.compile(x) for x in source.get("include_path_regex", [])]
    excludes = [re.compile(x) for x in source.get("exclude_path_regex", [])]
    map_includes = [re.compile(x) for x in source.get('sitemap_include_path_regex', [])]
    map_excludes = [re.compile(x) for x in source.get('sitemap_exclude_path_regex', [])]
    empty_maps = set(source.get('allow_empty_sitemap_urls', [])) & set(declared)
    retained = False
    limit = int(source.get("max_sitemaps", 30))
    queue = [(checked_url(u), 0) for u in source["sitemap_urls"]]
    visited, maps, items = set(), [], {}
    while queue:
        url, depth = queue.pop(0)
        if declared and urllib.parse.urlsplit(url)[:2] != urllib.parse.urlsplit(declared[0])[:2]:
            raise CrawlFailure('error', 'Recovered sitemap child leaves configured origin', maps, len(items))
        if url in visited:
            continue
        if len(visited) >= limit or depth > MAX_DEPTH:
            raise CrawlFailure("incomplete", "Sitemap count/depth limit reached", maps, len(items))
        visited.add(url)
        if map_includes and not any(p.search(urllib.parse.urlsplit(url).path) for p in map_includes):
            maps.append({'url': url, 'status': 'excluded_scope'})
            continue
        if any(p.search(urllib.parse.urlsplit(url).path) for p in map_excludes):
            maps.append({'url': url, 'status': 'excluded_scope'})
            continue
        try:
            kind, entries = parse_sitemap(fetcher(url, options), int(options.get("max_bytes", 15000000)), allow_empty=url in empty_maps)
            if not entries:
                retained = True
                maps.append({'url': url, 'root': kind, 'entry_count': 0, 'status': 'empty_preserved',
                             'notice': 'Robots-declared optional empty inventory; prior scoped URLs retained, not currently observed'})
                continue
            maps.append({"url": url, "root": kind, "entry_count": len(entries), "status": "ok"})
            if kind == "sitemapindex":
                queue.extend((entry["url"], depth + 1) for entry in entries)
            else:
                for entry in entries:
                    path = urllib.parse.urlsplit(entry["url"]).path
                    if includes and not any(pattern.search(path) for pattern in includes):
                        continue
                    if any(pattern.search(path) for pattern in excludes):
                        continue
                    key = entry["url"]
                    if key in items and items[key]["lastmod"] != entry["lastmod"]:
                        # Overlapping maps may disagree: preserve newest metadata deterministically.
                        entry["lastmod"] = max(filter(None, [items[key]["lastmod"], entry["lastmod"]]), default=None)
                    items[key] = entry
        except Exception as error:
            maps.append({"url": url, "status": "error", "error": str(error)})
            raise CrawlFailure("error", str(error), maps, len(items)) from error
    observed_count = len(items)
    if retained and previous:
        # Older snapshots did not attribute URLs to individual maps. Retain the
        # prior scoped inventory conservatively rather than invent removals.
        for url, entry in previous.get('baseline', {}).items():
            path = urllib.parse.urlsplit(url).path
            if includes and not any(p.search(path) for p in includes):
                continue
            if any(p.search(path) for p in excludes):
                continue
            items.setdefault(url, {'url': url, 'lastmod': entry.get('lastmod')})
    for record in maps:
        if record.get('status') == 'empty_preserved':
            record['retained_scoped_url_count'] = len(items) - observed_count
    if not items:
        raise CrawlFailure("error", "No URLs match configured scope; empty observation retained as anomaly", maps, 0)
    return items, maps


def robots_sitemaps(source, options, fetcher):
    """Discover bounded replacement maps on the configured origin only."""
    origins = {urllib.parse.urlsplit(checked_url(url))[:2] for url in source['sitemap_urls']}
    if len(origins) != 1:
        raise ValueError('Robots recovery requires one configured origin')
    scheme, origin = next(iter(origins))
    robots = scheme + '://' + origin + '/robots.txt'
    limits = dict(options, max_bytes=min(int(options.get('max_bytes', 15000000)), 256000))
    body = fetcher(robots, limits)
    if len(body) > limits['max_bytes']:
        raise ValueError('Robots exceeds bounded size')
    urls = []
    for line in body.decode('utf-8-sig').splitlines():
        match = re.match(r'^\s*sitemap\s*:\s*(\S+)', line, re.I)
        if not match:
            continue
        url = checked_url(match.group(1))
        parsed = urllib.parse.urlsplit(url)
        if parsed[:2] != (scheme, origin):
            continue
        if parsed.fragment:
            raise ValueError('Sitemap URLs cannot contain fragments')
        if url not in urls:
            urls.append(url)
        if len(urls) > int(source.get('max_sitemaps', 30)):
            raise ValueError('Robots sitemap count exceeds configured limit')
    if not urls:
        raise ValueError('Robots declares no same-origin sitemap')
    return robots, urls


def crawl(source, options, fetcher=fetch_bytes, previous=None):
    try:
        return _crawl(source, options, fetcher, previous)
    except CrawlFailure as original:
        try:
            robots, urls = robots_sitemaps(source, options, fetcher)
            candidate = dict(source, sitemap_urls=urls)
            current, maps = _crawl(candidate, options, fetcher, previous, declared=urls)
        except Exception as recovery:
            extra = getattr(recovery, 'checked_maps', [])
            raise CrawlFailure(original.status, str(original) + '; robots recovery failed: ' + str(recovery),
                               original.checked_maps + extra, original.observed_count) from recovery
        maps.append({'url': robots, 'root': 'robots', 'status': 'recovered',
                     'previous_sitemap_urls': source['sitemap_urls'], 'sitemap_urls': urls,
                     'original_failure': str(original), 'previous_checked_maps': original.checked_maps})
        # Mutate only after the entire replacement set has passed validation.
        # The caller persists this source with the matching baseline transaction.
        source['sitemap_urls'] = urls
        return current, maps


def scope_hash(source):
    keys = ['sitemap_urls', 'include_path_regex', 'exclude_path_regex']
    keys.extend(key for key in ('sitemap_include_path_regex', 'sitemap_exclude_path_regex', 'allow_empty_sitemap_urls') if source.get(key))
    scope = {key: sorted(set(source.get(key, []))) for key in keys}
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def reconcile(source, previous, current, observed_at):
    scope = scope_hash(source)
    reset = previous is None or previous.get("scope_hash") != scope
    old = {} if reset else previous.get("baseline", {})
    seen = dict(previous.get("seen", {})) if previous else {}
    snapshot = {}
    for url, entry in current.items():
        first_seen = seen.setdefault(url, observed_at)
        snapshot[url] = {"url": url, "lastmod": entry["lastmod"], "first_seen": first_seen}
    added, changed, removed = [], [], []
    if not reset:
        for url in sorted(snapshot.keys() - old.keys()):
            entry = dict(snapshot[url])
            entry["is_first_seen"] = url not in previous.get("seen", {})
            entry["reappeared"] = not entry["is_first_seen"]
            added.append(entry)
        for url in sorted(snapshot.keys() & old.keys()):
            if snapshot[url]["lastmod"] != old[url].get("lastmod"):
                changed.append({"url": url, "old_lastmod": old[url].get("lastmod"), "new_lastmod": snapshot[url]["lastmod"], "first_seen": snapshot[url]["first_seen"], "metadata_only": True})
        removed = [old[url] for url in sorted(old.keys() - snapshot.keys())]
    state = {"schema_version": 1, "source_id": source["id"], "scope_hash": scope, "observed_at": observed_at, "baseline": snapshot, "seen": seen}
    result = {"id": source["id"], "name": source.get("name", source["id"]), "status": "ok", "observed_at": observed_at, "baseline_created": reset, "scope_reset": previous is not None and reset, "baseline": [snapshot[u] for u in sorted(snapshot)], "added": added, "changed": changed, "removed": removed, "notice": "首次基线不代表新趋势；first_seen是本机首次观测时间；changed仅为lastmod元数据变化。"}
    return state, result


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def process_source(source, options, state_dir, fetcher=fetch_bytes):
    identifier = source["id"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", identifier):
        raise ValueError("Source id must contain only letters, digits, underscore and hyphen")
    state_file = state_dir / (identifier + ".json")
    previous = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
    observed_at = utc_now()
    try:
        current, maps = crawl(source, options, fetcher, previous)
        state, result = reconcile(source, previous, current, observed_at)
        atomic_json(state_file, state)
        result["checked_maps"] = maps
        return result
    except CrawlFailure as error:
        return {"id": identifier, "name": source.get("name", identifier), "status": error.status, "error": str(error), "observed_at": observed_at, "baseline_created": False, "baseline_preserved": previous is not None, "baseline": [], "added": [], "changed": [], "removed": [], "observed_partial_count": error.observed_count, "previous_baseline_count": len(previous.get("baseline", {})) if previous else 0, "checked_maps": error.checked_maps}


def _main_unlocked(argv=None):
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=directory / "config.json")
    parser.add_argument("--site", action="append", default=[], help="Repeat to select source IDs, including disabled sources explicitly")
    parser.add_argument("--state-dir", type=Path, default=directory / "state")
    parser.add_argument("--output-dir", type=Path, default=directory / "results")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if config.get("schema_version") != 1:
        raise ValueError("Unsupported config schema_version")
    ids = [source["id"] for source in config["sources"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source IDs")
    unknown = set(args.site) - set(ids)
    if unknown:
        raise ValueError("Unknown source IDs: " + ", ".join(sorted(unknown)))
    sources = [s for s in config["sources"] if s["id"] in args.site] if args.site else [s for s in config["sources"] if s.get("enabled", False)]
    if not sources:
        raise ValueError("No sources selected")
    options = config.get("fetch", {})
    if int(options.get("max_bytes", 15000000)) <= 0 or float(options.get("timeout_seconds", 25)) <= 0 or float(options.get("request_delay_seconds", .25)) < 0:
        raise ValueError("Invalid fetch limits")
    results = []
    for source in sources:
        try:
            result = process_source(source, options, args.state_dir)
        except Exception as error:
            result = {"id": source["id"], "status": "error", "error": str(error), "baseline": [], "added": [], "changed": [], "removed": []}
        results.append(result)
        if result['status'] == 'ok' and any(m.get('status') == 'recovered' for m in result.get('checked_maps', [])):
            atomic_json(args.config, config)
        print(f"{source['id']}: {result['status']} baseline={len(result['baseline'])} added={len(result['added'])} changed={len(result['changed'])} removed={len(result['removed'])}")
        if result.get("error"):
            print("  " + result["error"])
    output = args.output_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    atomic_json(output, {"schema_version": 1, "observed_at": utc_now(), "sources": results})
    print("Result: " + str(output.resolve()))
    return 0 if all(result["status"] == "ok" for result in results) else 1


def main(argv=None):
    # The diagnostic sitemap CLI shares the exact production scan/review lock.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from .queue_store import locked, recover
    locator = argparse.ArgumentParser(add_help=False)
    locator.add_argument('--state-dir', type=Path, default=Path(__file__).resolve().parent / 'state')
    args, _ = locator.parse_known_args(argv)
    root = args.state_dir.resolve().parent.parent
    with locked(root):
        recover(root)
        return _main_unlocked(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print("Configuration/runtime error: " + str(error))
        raise SystemExit(1)
