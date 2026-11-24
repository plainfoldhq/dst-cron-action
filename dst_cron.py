#!/usr/bin/env python3
"""DST-aware cron check and late-run guard. Standard library only (Python 3.9+)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_now(text):
    if not text:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("now must include a UTC offset or Z")
    return dt.astimezone(timezone.utc)


def dst_decision(now_utc, tz_name, local_hour):
    """Return (run, local_time_iso, reason): run is True when the local hour in tz_name equals local_hour."""
    if not 0 <= local_hour <= 23:
        raise ValueError("local-hour must be 0-23")
    local = now_utc.astimezone(ZoneInfo(tz_name))
    ok = local.hour == local_hour
    reason = "local hour %02d in %s %s intended hour %02d" % (
        local.hour, tz_name, "matches" if ok else "differs from", local_hour)
    return ok, local.isoformat(), reason


def scheduled_time(now_utc, cron):
    """Most recent UTC time <= now matching the minute and hour fields of a 5-field cron string.

    Only numeric minute and hour fields are supported. Day fields are ignored, so a
    run more than 24 hours late looks less late than it is."""
    fields = cron.split()
    if len(fields) != 5 or not fields[0].isdigit() or not fields[1].isdigit():
        raise ValueError("schedule needs numeric minute and hour fields: %r" % cron)
    minute, hour = int(fields[0]), int(fields[1])
    if minute > 59 or hour > 23:
        raise ValueError("schedule minute/hour out of range: %r" % cron)
    cand = now_utc.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand > now_utc:
        cand -= timedelta(days=1)
    return cand


def delay_minutes(now_utc, cron):
    return (now_utc - scheduled_time(now_utc, cron)).total_seconds() / 60.0


def decide(now_utc, tz_name="", local_hour="", cron="", max_delay="", on_late="skip"):
    """Combine both checks. Returns dict with run, reason, local_time, delay."""
    if on_late not in ("skip", "warn"):
        raise ValueError("on-late must be skip or warn")
    run, reasons, local_time, delay, warn = True, [], "", "", None
    if local_hour != "" and not tz_name:
        raise ValueError("local-hour requires timezone")
    if tz_name:
        if local_hour == "":
            raise ValueError("local-hour is required with timezone")
        # With a schedule string, judge the time the cron was meant to fire, so a delayed
        # run is not mistaken for the other cron's hour.
        basis = scheduled_time(now_utc, cron) if cron else now_utc
        ok, local_time, why = dst_decision(basis, tz_name, int(local_hour))
        if cron:
            why += " (evaluated at scheduled time %s)" % basis.strftime("%H:%MZ")
        reasons.append(why)
        run = run and ok
    if max_delay != "":
        if not cron:
            raise ValueError("schedule is required with max-delay-minutes")
        limit = float(max_delay)
        if not (0 <= limit < float("inf")):
            raise ValueError("max-delay-minutes must be a finite number >= 0")
        d = delay_minutes(now_utc, cron)
        delay = "%.1f" % d
        if d > float(max_delay):
            msg = "started %.1f min after scheduled time (limit %s)" % (d, max_delay)
            reasons.append("late: " + msg)
            if on_late == "skip":
                run = False
            else:
                warn = msg
        else:
            reasons.append("on time (%.1f min after schedule)" % d)
    return {"run": run, "reason": "; ".join(reasons) or "no checks configured",
            "local_time": local_time, "delay": delay, "warn": warn}


def clean(text):
    """Make text safe for workflow commands and GITHUB_OUTPUT (one line, no %)."""
    return " ".join(text.replace("%", "pct").split())


def main():
    env = os.environ
    # Manual and other non-schedule events always run.
    event = env.get("GITHUB_EVENT_NAME", "schedule")
    try:
        now = parse_now(env.get("PF_NOW", ""))
        if event != "schedule" and not env.get("PF_NOW"):
            res = {"run": True, "reason": "event %s is not a schedule: checks skipped" % event,
                   "local_time": "", "delay": "", "warn": None}
        else:
            res = decide(now, env.get("PF_TIMEZONE", ""), env.get("PF_LOCAL_HOUR", ""),
                         env.get("PF_SCHEDULE", ""), env.get("PF_MAX_DELAY", ""),
                         env.get("PF_ON_LATE", "skip") or "skip")
    except (ValueError, ZoneInfoNotFoundError) as exc:
        print("::error::dst-cron: %s" % clean(str(exc)))
        return 1
    if res["warn"]:
        print("::warning::dst-cron: %s" % clean(res["warn"]))
    out = {"run": "true" if res["run"] else "false", "reason": clean(res["reason"]),
           "local-time": res["local_time"], "delay-minutes": res["delay"]}
    for k, v in out.items():
        print("%s=%s" % (k, v))
    path = env.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            for k, v in out.items():
                fh.write("%s=%s\n" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main())
