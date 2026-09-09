# CI resource monitoring

Tracks what the repository's CI costs and, more usefully, what changed: which jobs got slower
or hungrier than they were, which are close to the runner's memory limit, and where the
runner-minutes go.

**To read it:** open the newest [`ci-usage`](workflows/ci-usage.yml) run and read its job
summary. That is the whole interface — nothing is committed to the repository.

**To see one job's own numbers:** open the job and scroll to the bottom of its summary. Every
job prints its own wall time, CPU time and peak memory the moment it ends, including jobs on
pull-request branches, which the nightly trend does not sweep.

## What is measured where

| number | source | coverage |
| --- | --- | --- |
| wall time per job and per step | GitHub jobs API | every job of every run, retroactively |
| peak memory | cgroup `memory.peak`, via the resource-monitor action | jobs with a checkout |
| real CPU time | cgroup `cpu.stat`, same action | jobs with a checkout |

GitHub reports none of the last two anywhere, which is why the action exists. It reports the
first, so no instrumentation is needed for durations and the report covers history that
predates this monitoring.

The billing endpoint (`/actions/runs/{id}/timing`) is deliberately not consulted. This
repository is public, Actions minutes are therefore free, and the endpoint answers that every
run cost zero. "Runner-minutes" here means wall-clock time on a runner, computed from job
timestamps — the thing that actually costs something, in queueing and in how long a pull
request waits.

## Adding the monitor to a new job

Two steps, next to the checkout and at the end, exactly like the `hisim-cache` action:

```yaml
    - uses: actions/checkout@v6
    - name: Start the resource monitor
      uses: ./.github/actions/resource-monitor
      with:
        mode: start
        scope: ${{ matrix.name }}      # omit for a job with no matrix siblings
    ...
    - name: Report the resource usage
      if: always()                     # a job killed for running out of memory is the one that matters
      uses: ./.github/actions/resource-monitor
      with:
        mode: report
        scope: ${{ matrix.name }}      # must match the start call
```

The probe cannot fail a job: every error is caught, reported as a `::warning::` and exited 0.
A job without a checkout cannot use the action at all, since the action itself lives in the
repository; the four `summary` jobs are in that position and are covered for duration only.

## What gets flagged

Thresholds live in `RegressionRules` in `scripts/ci_usage_report.py`:

- **slower**: median duration up ≥ 35% *and* ≥ 45 s, over ≥ 3 recent and ≥ 5 baseline runs
- **hungrier**: median peak memory up ≥ 25% *and* ≥ 512 MB
- **near the limit**: any job reaching ≥ 85% of the runner's RAM (16 GB on `ubuntu-latest`)
- **holding a runner idle**: ≥ 5 min jobs using < 35% of the runner's cores

Both a proportional and an absolute threshold have to be crossed, and only successful runs
form a baseline. Shared runners vary by tens of percent for reasons nobody controls, a
cancelled run says nothing about cost, and a report that flags either is one people stop
opening.

## Running the collector by hand

```bash
GH_TOKEN=<a token with actions:read> python3 scripts/ci_usage_report.py \
    --window-days 30 --recent-days 3 --out report.md --index ci-usage-index.json
```

Without a token it runs unauthenticated at 60 requests/hour, which is enough to try but not
to sweep. Useful flags: `--memory-branch ''` collects memory artifacts from every branch
rather than `main` alone, `--max-requests` and `--max-artifact-downloads` cap the sweep.

## Why the sweep is incremental

At roughly 150 runs a day and ten jobs each, re-reading a month would want some 9,000 API
requests; `GITHUB_TOKEN` allows 1,000 an hour. So each night downloads the previous night's
index from its own artifact, reads only runs newer than its watermark (less an eight-hour
overlap, for runs still in flight last night), prunes to the window and uploads it again. A
typical night costs about 200 requests.

Memory artifacts are the expensive half — one download each, about 1,500 a day — so they are
collected for `main` and for flagged jobs rather than for everything. Pull-request jobs still
produce and print their own numbers; they just don't feed the nightly trend.

When the budget runs out mid-sweep the report says so under **Collection** and covers less
than its window claims. That is deliberate: a partial report is worth having, and a nightly
job that goes red because GitHub was busy trains everyone to ignore it.

## Known limits

- The measurement starts at checkout, so the runner booting and the container image being
  pulled fall outside it. The gap shows up as the difference between the job duration the API
  reports and the wall time the probe reports.
- `memory.peak` cannot always be reset (it needs kernel 6.8+ and a writable mount). Where it
  cannot, the peak includes what the runner agent was already using; the record carries
  `baseline_memory_bytes` so the rise can still be read off.
- Where cgroup files are unreadable entirely, a 0.5 s sampler stands in. It measures the whole
  machine and misses shorter spikes, and every record says which method produced it —
  `peak_memory_method` — so sampled and exact numbers are never silently compared.
- The index lives in an artifact with a 14-day retention. Losing it costs a cold start, not
  the history: the API still holds the runs, and the following nights fill the window back in.
