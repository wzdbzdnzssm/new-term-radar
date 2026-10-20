# Local sitemap monitor bridge

Standalone invocation (no Hugging Face, GitHub, website requests, or paid APIs):

```bash
/home/admin/.hermes/venvs/new-term-radar/bin/python -m radar.monitor_bridge \
  --monitor-root /home/admin/.hermes/data/ai-video-monitor \
  --state /home/admin/new-term-radar/.bridge-state \
  --output /home/admin/new-term-radar/bridge-output
```

Run from `/home/admin/new-term-radar`. Outputs: `sitemap-monitor.json` and
`sitemap-monitor.md`; isolated state: `baseline.json` and `bridge.lock` under
`--state`. Exit 0 means full coverage; 1 means degraded coverage; 2 means busy or
invocation/output failure. Freshness defaults to 36 hours (`--max-age-hours`).

The bridge reads monitor config, latest scan, progress when provided, committed
per-source receipts, and digest-matched source baseline maps. It never writes to
the monitor tree. Failed, uncommitted, stale, running, or malformed sources retain
their previous bridge snapshots. Missing/corrupt global inputs or mid-read changes
produce explicit degradation without updating baselines. A corrupt bridge baseline
is preserved for manual investigation, not silently reset. Writes use atomic file
replacement, not a multi-file transaction; an interrupted run can be repeated.
Use one bridge state directory per output stream; a nonblocking lock serializes
runs sharing that directory.

First successful observation of each source establishes a baseline with zero
bridge additions. Scope changes reset that source, also with zero additions.
Later additions mean exact URLs added since the last accepted bridge snapshot,
not necessarily pages added in the latest upstream run. Repeat reads produce zero
bridge additions. Receipt `added` URLs/counts are exposed separately as verified
monitor-run reference and may repeat when the same monitor run is read again.
Pending observations are historical backlog and are not used. No canonical IDs or
aliases exist in the inspected config/snapshot/receipt schema; identity is
conservatively exact URL, without inferred multilingual or semantic equivalence.

URL text is an unevaluated candidate, not a validated term. `first_seen` and
`lastmod` are not publication dates. Multiple domains do not establish independent
businesses. No webpage bodies are copied. This channel never enters native lead
history or MCP results. Zero candidates with incomplete coverage is not evidence
of no new terms.

The existing explicit free daily runner now invokes this local bridge on every
run and includes its status, coverage, errors and report references in both the
timestamped radar Markdown/JSON pair and `latest.md`/`latest.json`. HF/GitHub
failure or timeout still publishes an explicit degraded report with bridge status.
Its runtime bridge state is `/home/admin/.hermes/data/new-term-radar/monitor-bridge`
and reports are under `/home/admin/.hermes/outputs/new-term-radar`.

```bash
/home/admin/.hermes/venvs/new-term-radar/bin/python /home/admin/.hermes/scripts/new_term_radar_daily.py
```

The latter command does fetch HF/GitHub. The bridge itself is read-only. AI
opportunity collection and review now belong to `radar.ai_monitor`, with the
existing state root retained and Hermes responsible for scheduling and verified
delivery. See [AI opportunity operations](ai-opportunity-monitor.md) for its
four-hour silent collection and 08:00 review schedule. The bridge continues to
report coverage and never starts a second collector.
