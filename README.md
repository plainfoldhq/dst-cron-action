# DST-aware cron and late-run guard for GitHub Actions

Free, MIT, standard library only (Python 3.9+ and system tzdata; works on GitHub-hosted ubuntu and macOS runners; not tested on Windows runners, which lack `python3`).

GitHub `schedule` cron runs in UTC only, so a job meant for "3:00 AM Chicago" moves an hour against local time twice a year. Scheduled runs can also start late. This action fixes both:

1. **DST-aware cron.** Schedule two UTC crons, one for each side of DST. The action sets `run=true` only on the one that lands in your intended local hour.
2. **Late-run guard.** Skip the job, or just warn, when the run started more than N minutes after its scheduled time.

## Use

```yaml
name: nightly
on:
  schedule:
    - cron: "0 8 * * *"   # 03:00 in America/Chicago during daylight time (UTC-5)
    - cron: "0 9 * * *"   # 03:00 in America/Chicago during standard time (UTC-6)
  workflow_dispatch:

jobs:
  guard:
    runs-on: ubuntu-latest
    outputs:
      run: ${{ steps.dst.outputs.run }}
    steps:
      - id: dst
        uses: plainfoldhq/dst-cron-action@v1
        with:
          timezone: America/Chicago
          local-hour: "3"
          schedule: ${{ github.event.schedule }}
  work:
    needs: guard
    if: needs.guard.outputs.run == 'true'
    runs-on: ubuntu-latest
    steps:
      - run: echo "runs once, at 03:xx Chicago time, all year"
```

Always pass `schedule: ${{ github.event.schedule }}` (as above) so a delayed run is judged by the time its cron was meant to fire. Without it the action uses the current time, and a run delayed past the hour boundary is skipped, or the other cron's delayed run also fires.

Pick the two UTC hours as `local-hour` minus the zone's two UTC offsets (Chicago: 3+5 = 8 and 3+6 = 9). Check yours at [plainfoldhq.github.io/dst-dates](https://plainfoldhq.github.io/dst-dates/).

### With the late-run guard

```yaml
      - id: dst
        uses: plainfoldhq/dst-cron-action@v1
        with:
          timezone: America/Chicago
          local-hour: "3"
          schedule: ${{ github.event.schedule }}   # the cron string that fired
          max-delay-minutes: "30"
          on-late: skip                            # or: warn
```

Guard only (no timezone): omit `timezone` and `local-hour`.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `timezone` | empty | IANA zone, e.g. `Europe/Berlin`. Empty skips the DST check. |
| `local-hour` | empty | Intended local hour, 0-23. Required with `timezone`. |
| `schedule` | empty | `github.event.schedule`. Needed for the guard; minute and hour fields must be plain numbers. |
| `max-delay-minutes` | empty | Guard threshold. Empty turns the guard off. |
| `on-late` | `skip` | `skip` sets `run=false`; `warn` keeps `run=true` and adds a warning annotation. |
| `now` | empty | Override current UTC time (ISO 8601 with `Z`), for tests. |

Outputs: `run` (`true`/`false`), `reason`, `local-time`, `delay-minutes`.

## Behavior and limits

- `local-hour` without `timezone`, a bad zone or a non-finite `max-delay-minutes` fail the step with an error.
- Events other than `schedule` (for example `workflow_dispatch`) always give `run=true`, unless `now` is set.
- The DST check compares the local **hour** only. Your cron minute sets the minute.
- On the autumn "fall back" day the local hour repeats. If `local-hour` is that hour, both crons can match on that day, so the job can run twice. Hours that are skipped in spring ("spring forward") never match, so the job does not run that day. Pick an hour that does not fall in your zone's transition window to avoid both.
- The guard measures lateness against the most recent time matching the cron's minute and hour in UTC. Day-of-week and day-of-month fields are ignored, so a run more than 24 hours late looks less late than it is.
- GitHub may drop or heavily delay scheduled runs at busy times. The guard cannot start a run that never happened; it only decides about runs that did.
- Time zone rules come from the runner's tzdata. Results are as current as the runner image.

## Tests

```
python3 -m unittest discover -s tests -v
```

The tests check hand-worked DST cases, and compare the action's hour around every transition of eight zones (2024-2027, hourly, +/- 30 hours) with the system C library (`date`); `zdump` supplies the transition times. Both read the same system tzdata files, so the tests verify the logic, not the tzdata release.

## More

- Scheduled-agent tooling: [agent-ops-kit](https://github.com/plainfoldhq/agent-ops-kit)
- DST dates by country and year: [plainfoldhq.github.io/dst-dates](https://plainfoldhq.github.io/dst-dates/)

MIT licensed. Provided as is.
