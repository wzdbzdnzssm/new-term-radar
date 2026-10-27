# Unified AI opportunity monitor

`radar.ai_monitor` owns AI opportunity sitemap collection, named video template
discovery, evidence compaction, human review, stable identities and outbox state.
Hermes schedules commands and retains the existing verified delivery workflow.
No CLI command here sends messages, starts paid generation, or deploys websites.
Financial, server/API health and Sub2API catalog monitors are outside this module.

An optional `review_workflow.path_first_from` date in `monitor/config.json` enables
URL-first discovery from that Shanghai calendar day. Readable final path segments
provide names directly; opaque IDs fall back to detail-page reads, unless an already
collected named template card identifies them. These compact `discovery_name`
records explicitly do not verify HTML, current detail availability or publication.
Existing baseline-addition receipts still bind observed new supply; initial
inventory, restoration and locale aliases do not become new demand. A source
addition can rank for review without claiming that the AI demand was born recently.
Hermes confirms candidate name groups and writes one keyword brief per stable need.
Evidence history is indexed once per submit batch, retaining earlier evidence.
Without the date setting, the existing page-based workflow remains in effect.
`review_workflow.include_first_cross_platform` also admits previously unreported
cross-platform opportunities without requiring a new publication in this window.
Set `delivery_receipts_dir` to the existing daily delivery receipts directory;
only complete deliveries with verified cloud and sent-copy hashes establish prior
business publication. Internal opportunity ACKs do not mean the word was emailed.
Optional `manual_delivery_receipts_dir` points to separate `manual_trial` receipts.
Only complete deliveries with matching body/cloud/sent-copy hashes and confirmed
channel statuses count from the following Shanghai day. Their explicit
`published_need_ids` preserve publication history; `published_items[].source_urls`
contains `{source_id, url}` pairs to recognize the same subject if a later review
rebuilds its need ID. Already delivered source URLs cannot become new-supply or
recurrence signals merely because production first discovers them later. Real new
source URLs remain eligible for ordinary evidence-bound review.
Historical words are labelled as first business inclusion, without recent-growth
or first-launch claims. Same-day reruns retain their original report window.
Previously published words with a real new source/page addition use a compact
recurrence line. The new appearance is validated against its collection receipt;
the line names that platform and distinguishes cumulative platform coverage.
Existing unchanged pages do not alone generate recurrence notifications.

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
Sitemap failures trigger one bounded same-origin `/robots.txt` refresh. Replacement
maps retain configured sitemap/path filters. Audited map exclusions apply to
both root replacement and index children; raw robots declarations remain evidence
in `robots_sitemap_urls`, separately from effective `sitemap_urls`. Unknown maps
remain eligible for validation. Replacement roots are committed with the new
baseline only after complete validation. Recovery receipts retain the original
failure; failed recovery preserves both config and baseline. A changed scope builds
inventory without creating new-demand observations.

The full 59-source map audit removes only exhaustively verified unrelated roots
(time-zone conversions, author profiles, image-only API/payment navigation, and
font/color inventories). Mixed maps remain. Higgsfield supplemental maps retain
video-generation documentation, mixed-media videos and future contest inventory.

Picsart retains eight raw robots declarations but six effective roots. Its mixed
`general-library` index stays active for effects, workflows, video templates and
unknown future children; only five verified asset/navigation child maps and blog
archive maps are excluded. Fonts and colors are removed from active roots. Only its explicitly configured miniapp map may be a
valid empty `urlset`, and only after robots confirms that declaration. Such receipts
say `empty_preserved`; prior scoped URLs are conservatively retained because legacy
snapshots lack per-map attribution. They are historical inventory, not a current
page-availability claim. Invalid XML, an empty index and HTTP failures remain errors.
An unrelated empty sitemap is still anomalous. Locale aliases retain stable identity.

The server daily job and both opportunity-gate commands use the existing Hermes
Python environment (`/home/admin/.hermes/hermes-agent/venv/bin/python`), which has
its required scheduler dependencies. The gate still requires real semantic review
and its existing three-part opportunity and native-execution checks.

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
includes all pending discoveries and historical backlog (discovery does not
prove demand age), with recent and source-fair work first. Review batches contain up
to 16 entries; this is a fetch batch size, not a daily review or P1 output cap.
Submissions still use the existing evidence and semantic review contract. Progress
counts only accepted/watch/excluded decisions submitted after the plan began;
blocked items and unfinished batches remain incomplete. Late arrivals extend the
same plan. Collection receipts use execution-specific batches and snapshots captured
inside the collectors' locks, so a later silent scan cannot replace the bound facts.
If a current subject page is unavailable, `review_pages --root ROOT
--history-for-observation ID` lists only existing hash-verified metadata bound to
that observation's URLs. Historical captures can support historical observation
review, but never establish current availability or fresh publication. Current
HTTP failures stay recorded; the query does not fetch, classify or submit decisions.

Hermes' AI-only completion route writes only eligible P1/P2 keyword briefs in the report window
to the existing cloud table and privately configured email recipient, deduplicated
by stable need ID without a P1 count limit. Reports use four columns: main term,
related terms, long-tail terms and source platforms. New supply and historical
cross-platform first inclusion have separate tables. Previously delivered same-day
words retain only their name and sources; recurrence rows contain only the name,
this round's platform and cumulative platform count. SEO advice and internal signal
reviews stay out of the report. Suggestions may be empty when unsupported; an SEO
advice field is optional and retained only for old brief compatibility. Suggestions
are not observed search queries.
Evidence URLs, P3 observations, collection/review counts and failures remain internal.
After each semantic review batch, use `python -m radar.ai_monitor.keyword_brief candidates
--root ROOT --execution-id ID`, then `submit --root ROOT --input FILE` with
evidence-bound briefs and `preview --root ROOT --execution-id ID`. Briefs live in
`keyword-briefs/` and never modify the original outbox or ACK. Every selected P1/P2
needs an independent `report_signal` include/exclude review bound to its item hash,
execution, report window and verified evidence. Internal historical P2 status or a
late first review is not a new report signal. Use `python -m radar.ai_monitor.report_signal
candidates --root ROOT --execution-id ID`, then semantic `submit --input FILE`
and `verify` with the same root/execution arguments. Included items need a valid
brief; explicitly excluded historical stock does not. A missing review/brief is a
recoverable internal failure, not a no-keyword report. Only a complete run without included terms emits
`本次暂无可发布的新趋势词`.
Short reports are inline; long reports use a keyword overview in the email body
plus a UTF-8 attachment identical to the complete cloud keyword report.
The next report starts at the prior report day's confirmed coverage cutoff; same-day
reruns retain that day's terms only when their original real signal passes the same
review. New supply can cite normal committed baseline additions (not initial scope,
inventory or restored URLs), even without a detail publication date. Higgsfield and
the three selected Pippit SSR pages persist small future `supply-deltas/` facts in their ordinary report transactions;
missing historical pre-states are never reconstructed. Frozen reports record their exact
window. Incomplete coverage, planned review, or remaining pending blocks every final
keyword report, including a no-keyword result. Cloud and email have
separate durable receipts; uncertain email sends reconcile the frozen sent body
and download the sent attachment to verify its full-content hash before any resend. A sent-folder receipt confirms provider acceptance/sent copy,
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
