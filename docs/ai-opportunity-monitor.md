# Unified AI opportunity monitor

`radar.ai_monitor` owns AI opportunity sitemap collection, named video template
discovery, evidence compaction, human review, stable identities and outbox state.
Hermes schedules commands and retains the existing verified delivery workflow.
No CLI command here sends messages, starts paid generation, or deploys websites.
Financial, server/API health and Sub2API catalog monitors are outside this module.

```bash
python -m radar.ai_monitor daily --root /path/to/ai-video-monitor
python -m radar.ai_monitor core --root /path/to/ai-video-monitor
python -m radar.ai_monitor scan --root /path/to/ai-video-monitor
python -m radar.ai_monitor templates --root /path/to/ai-video-monitor --tier core
python -m radar.ai_monitor pippit --root /path/to/ai-video-monitor
python -m radar.ai_monitor review --root /path/to/ai-video-monitor export
python -m radar.ai_monitor evidence --root /path/to/ai-video-monitor --observation-id ID
python -m radar.ai_monitor.review_plan batch --root /path/to/ai-video-monitor --execution-id ID
```

On the existing server, the only state directory remains
`/home/admin/.hermes/data/ai-video-monitor`; repository source is
`/home/admin/new-term-radar/radar/ai_monitor`. Former Python entries are compatibility
wrappers importing this package, rather than a second implementation. Runtime data,
source config, journals, historical queues, stable observation/need IDs, aliases,
first_seen, outbox bodies/hashes and verified receipts stay in place. Do not put them
in Git or copy an old local repository over the server repository.

The original 58 sources plus the scoped Pippit article source make 59 configured
sources when Pippit is enabled. They run daily at 08:00 Asia/Shanghai, followed by
Higgsfield and Pippit supplemental discovery. Every four hours `core` checks
Higgsfield's motion/effects/apps sitemaps and apps/effects aggregate cards, plus
Pippit's three selected AI video catalogs, and silently queues discoveries. All
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
The coordinator's `latest-scan.json` records the source scope actually covered by
that batch; `latest-template-scan.json` reports Higgsfield supplementary coverage,
and `latest-pippit-template-scan.json` reports the three Pippit catalogs. An older
58-source manifest does not establish coverage of a newly configured 59th source.

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

The native daily pre-run persists a review plan bound to its verified execution. It
includes every pending discovery first seen within 48 hours (discovery does not
prove demand age), followed by source-fair backlog work. Review batches contain up
to 16 entries; this is a fetch batch size, not a daily review or P1 output cap.
Submissions still use the existing evidence and semantic review contract. Progress
counts only accepted/watch/excluded decisions submitted after the plan began;
blocked items and unfinished batches remain visible and rotate in later plans.

Hermes' AI-only completion route writes the same readable daily summary to the
existing cloud table and the privately configured email recipient, including runs
with no new eligible outbox. It reports collection coverage, reviewed and remaining
work, qualifying reports, observations and gaps. Incomplete planned review or
coverage cannot become a successful no-opportunity result. Cloud and email have
separate durable receipts; uncertain email sends reconcile the exact sent copy
before any resend. A sent-folder receipt confirms provider acceptance/sent copy,
not recipient inbox placement. Existing opportunity outboxes and their ACKs remain
immutable and continue through the original publication contract.

## Pippit: a narrow article and AI-video scope

The official source is Pippit (`www.pippit.ai`). Its one English article sitemap is
`https://www.pippit.ai/sitemap/new/573081/default/index0.xml`; configuration permits
only `/blog`, `/resource`, `/tools` and `/create`. The inspected snapshot contains
8,966 URLs, with 8,846 matching these paths, including official legacy URLs at
`pippit.capcut.com`. These are one Pippit source, not independent businesses.
Article additions are reviewed for video creation intent; general commerce and
image content are supplemental evidence, rather than assumed video templates.
Neither the word AI in a title nor multiple new URLs is an admission requirement.

The only template catalog pages are:

- `https://www.pippit.ai/templates/trending-ai-new-templates-2026`
- `https://www.pippit.ai/templates/ai-generated-new-trending-video`
- `https://www.pippit.ai/templates/free-ai-video-templates-trending`

The parser reads only `script#__MODERN_ROUTER_DATA__` JSON at
`loaderData['templates_(keyword)/page'].project.kep.templates.video`. Each inspected
page contains 40 video cards. Image cards, config templates, related tags and
navigation do not enter the pool. A nonempty video URL and positive duration are
required, but media is never downloaded. The 19-digit string `web_id` is the stable
entity key. `short_title` supplies the name (including HOTEL LOBBY without AI), and
`short_title_en` supplies the detail-page slug. Slug changes retain one observation
ID and all URL aliases. Fingerprints ignore media signatures, query parameters,
CDN host rotations and platform usage/play counters.

`pippit-template-state.json` keeps per-page snapshots and historical entity IDs.
First readable snapshots establish inventory without flooding pending. Cross-page
duplicates merge, and disappearing/reappearing cards are not new discoveries.
Generic New Trend/edit titles remain explicitly unclear rather than confirmed named
playgrounds. Source upload dates and usage/play counters do not prove demand age,
search demand or independent end-user interest. Every new named candidate enters
the original pending/review/need_id/outbox chain, with unknown demand and generation
validation retained. `review export` includes `pippit_template_coverage` alongside
the shared candidates. Invalid/empty SSR preserves the prior page snapshot and
reports partial coverage, rather than claiming no changes.

This deliberately does not scan arbitrary shards or the 335-shard English template
library and is not an exhaustive, verified ranking of newly published trends.
Initialize the new article source with a worker receipt under the existing locks;
retain the previous full-batch manifest until an actual configured full batch runs.
Do not replace it with a single-source 1/1 manifest or reinitialize the original
58 baselines. The `pippit.ARTICLE_SOURCE` constant defines the exact configuration;
the daily/core orchestration activates Pippit only after that source is enabled.
