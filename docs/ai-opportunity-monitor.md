# Unified AI opportunity monitor

`radar.ai_monitor` owns AI opportunity sitemap collection, named video template
discovery, evidence compaction, human review, stable identities and outbox state.
Hermes schedules commands and retains the existing verified delivery workflow.
No CLI command here sends messages, starts paid generation, or deploys websites.
Financial, server/API health and Sub2API catalog monitors are outside this module.

```bash
python -m radar.ai_monitor daily --root /path/to/ai-video-monitor
python -m radar.ai_monitor scan --root /path/to/ai-video-monitor
python -m radar.ai_monitor templates --root /path/to/ai-video-monitor --tier core
python -m radar.ai_monitor review --root /path/to/ai-video-monitor export
python -m radar.ai_monitor evidence --root /path/to/ai-video-monitor --observation-id ID
```

On the existing server, the only state directory remains
`/home/admin/.hermes/data/ai-video-monitor`; repository source is
`/home/admin/new-term-radar/radar/ai_monitor`. Former Python entries are compatibility
wrappers importing this package, rather than a second implementation. Runtime data,
source config, journals, historical queues, stable observation/need IDs, aliases,
first_seen, outbox bodies/hashes and verified receipts stay in place. Do not put them
in Git or copy an old local repository over the server repository.

The original 58 configured sources run daily at 08:00 Asia/Shanghai, followed by
Higgsfield supplemental discovery. Every four hours only the core motion/effects/apps
sitemaps and apps/effects aggregate cards are checked and silently queued. All
candidate evidence is reviewed at 08:00 before unified notifications. Daily
runs cover the 08:00 core slot themselves; the separate core job runs at
00:00/04:00/12:00/16:00/20:00 to avoid a competing 08:00 coordinator. Sunday daily
runs include the weekly supplemental maps. Collection frequency is not notification
frequency. The daily review reads both the normal observations and template fields
from `review export`, submits through the same review/need_id/outbox contract, and
keeps every qualifying P1 even if an action summary contains at most five items.

`scan` resumes a date/configuration batch and skips already committed sources. A
700 MiB worker limit applies per source. Transactions stream canonical JSON hashing
with the original digest byte format; receipts remain replayable. Both scan modes
share `scheduled-scan.lock` and the queue lock. Busy exits 75. Nonzero source errors,
partial coverage, or unread dynamic catalogs are never a no-opportunity finding.
The coordinator's `latest-scan.json` is authoritative for the 58-source batch;
`latest-template-scan.json` separately reports every supplementary feed and catalog.

The 13 Higgsfield sitemap routes have independent first-run inventories, so added
coverage does not reset the existing source scope or announce historical URLs as new.
One new template URL or card suffices; UUID page names come from H1, structured
entity names or page titles. Card fingerprints also detect additions to unchanged
aggregate URLs. Empty/unread catalogs preserve prior snapshots and report `unread`.
The known empty contests sitemap is allowed as inventory; Genjutsu is monitored as
a supplemental capability page and is explicitly not claimed to be a template catalog.
Only metadata, summaries of at most 500 characters and fingerprints persist.

Templates live in `video-template-candidates.json` and enter the existing pending
queue for review. Marketing/blog/changelog/preset changes enter the same queue as
supplemental evidence, without being called templates automatically. A template UUID
or normalized locale route links the same entity; unresolved cross-URL semantic
aliases must be merged to an existing `need_id` during review. One vendor's locales,
blogs, cards and subdomains count as one source group. Names such as Bulin 47 retain
their digits. Existing reported entities are checked before any new report.

Discovery time, source lastmod/publication, production intent, independent end-user
demand, implementation feasibility and real generation validation remain separate.
Unknown facts remain unknown. Static photography and model/developer terms use
separate lanes. Handoffs record 024 as the base, 025 as a fixed-action/BGM reference,
reference rights and generation validation. A handoff never makes a site launch-ready.
The original P1/P2/P3, live full-page verification, historical-age veto, stable outbox
deduplication and real delivery receipt requirements remain in force.
