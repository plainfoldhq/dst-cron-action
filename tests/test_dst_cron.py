"""Tests. DST decisions are checked against two sources:
1. zoneinfo (what the action uses), via known hand-computed cases;
2. the system C library and zdump (`TZ=zone date -d @epoch`, `zdump -v`), which
   do not use Python. Both read the system tzdata files, so a wrong tzdata release
   would affect both; they check the implementations, not the data."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import dst_cron as d  # noqa: E402

HAVE_TOOLS = bool(shutil.which("zdump") and shutil.which("date"))
ZONES = ["America/Chicago", "America/New_York", "Europe/London", "Europe/Berlin",
         "Australia/Sydney", "Asia/Kolkata", "America/Sao_Paulo", "Pacific/Auckland"]


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def system_local_hour(zone, when):
    out = subprocess.run(["date", "-d", "@%d" % int(when.timestamp()), "+%H"],
                         env={"TZ": zone, "PATH": os.environ["PATH"]},
                         capture_output=True, text=True, check=True)
    return int(out.stdout)


def zdump_transitions(zone, y0, y1):
    out = subprocess.run(["zdump", "-v", "-c", "%d,%d" % (y0, y1 + 1), zone],
                         capture_output=True, text=True, check=True).stdout
    res = []
    for line in out.splitlines():
        if " UT = " not in line:
            continue
        left = line.split(" UT = ")[0].split(None, 1)[1].strip()
        res.append(datetime.strptime(left, "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc))
    return res


class KnownCases(unittest.TestCase):
    def test_chicago_two_crons_across_dst(self):
        # Intended 03:00 local Chicago. Crons at 08 UTC (CDT, UTC-5) and 09 UTC (CST, UTC-6).
        # Winter (CST, UTC-6): 09:00 UTC is 03:00 local.
        self.assertTrue(d.decide(utc(2026, 1, 15, 9), "America/Chicago", "3")["run"])
        self.assertFalse(d.decide(utc(2026, 1, 15, 8), "America/Chicago", "3")["run"])
        # Summer (CDT, UTC-5): 08:00 UTC is 03:00 local.
        self.assertTrue(d.decide(utc(2026, 7, 15, 8), "America/Chicago", "3")["run"])
        self.assertFalse(d.decide(utc(2026, 7, 15, 9), "America/Chicago", "3")["run"])

    def test_us_spring_forward_2026(self):
        # 2026-03-08 02:00 CST -> 03:00 CDT (08:00 UTC). 07:59 UTC is 01:59 CST.
        self.assertEqual(d.dst_decision(utc(2026, 3, 8, 7, 59), "America/Chicago", 1)[0], True)
        self.assertEqual(d.dst_decision(utc(2026, 3, 8, 8, 0), "America/Chicago", 3)[0], True)
        self.assertEqual(d.dst_decision(utc(2026, 3, 8, 8, 0), "America/Chicago", 2)[0], False)

    def test_us_fall_back_2026_repeated_hour(self):
        # 2026-11-01: 06:00 UTC 01:00 CDT, 07:00 UTC 01:00 CST. Hour 1 matches twice.
        self.assertTrue(d.dst_decision(utc(2026, 11, 1, 6), "America/Chicago", 1)[0])
        self.assertTrue(d.dst_decision(utc(2026, 11, 1, 7), "America/Chicago", 1)[0])
        self.assertFalse(d.dst_decision(utc(2026, 11, 1, 8), "America/Chicago", 1)[0])

    def test_no_dst_zone(self):
        for m in (1, 7):
            self.assertTrue(d.decide(utc(2026, m, 1, 3, 30), "Asia/Kolkata", "9")["run"])

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            d.decide(utc(2026, 1, 1), "America/Chicago", "24")
        with self.assertRaises(ValueError):
            d.decide(utc(2026, 1, 1), "America/Chicago", "")
        with self.assertRaises(ValueError):
            d.decide(utc(2026, 1, 1), "", "", "", "5", "skip")  # delay without schedule
        with self.assertRaises(ValueError):
            d.decide(utc(2026, 1, 1), on_late="explode")


class Delayed(unittest.TestCase):
    def test_delayed_run_judged_at_scheduled_time(self):
        # Winter 09:00Z cron started at 10:05Z is still the 03:00 CST run; the 08:00Z cron is not.
        r = d.decide(utc(2026, 1, 15, 10, 5), "America/Chicago", "3", "0 9 * * *")
        self.assertTrue(r["run"])
        r = d.decide(utc(2026, 1, 15, 9, 20), "America/Chicago", "3", "0 8 * * *")
        self.assertFalse(r["run"])
        # Summer: 08:00Z cron delayed past 09:00Z still runs; 09:00Z cron never does.
        self.assertTrue(d.decide(utc(2026, 7, 15, 9, 10), "America/Chicago", "3", "0 8 * * *")["run"])
        self.assertFalse(d.decide(utc(2026, 7, 15, 9, 10), "America/Chicago", "3", "0 9 * * *")["run"])

    def test_repeated_hour_runs_twice_documented(self):
        # Fall back 2026-11-01, local hour 1 occurs at 06Z and 07Z: both crons match.
        self.assertTrue(d.decide(utc(2026, 11, 1, 6), "America/Chicago", "1", "0 6 * * *")["run"])
        self.assertTrue(d.decide(utc(2026, 11, 1, 7), "America/Chicago", "1", "0 7 * * *")["run"])

    def test_bad_delay_values(self):
        for bad in ("nan", "-1", "inf"):
            with self.assertRaises(ValueError):
                d.decide(utc(2026, 1, 1, 6), cron="0 6 * * *", max_delay=bad)

    def test_hour_without_timezone(self):
        with self.assertRaises(ValueError):
            d.decide(utc(2026, 1, 1), "", "3")


class Guard(unittest.TestCase):
    def test_delay(self):
        self.assertAlmostEqual(d.delay_minutes(utc(2026, 5, 5, 6, 12), "0 6 * * *"), 12)
        self.assertAlmostEqual(d.delay_minutes(utc(2026, 5, 5, 5, 59), "0 6 * * *"), 23 * 60 + 59)
        self.assertAlmostEqual(d.delay_minutes(utc(2026, 5, 5, 6, 0), "0 6 * * 1-5"), 0)

    def test_skip_and_warn(self):
        now = utc(2026, 5, 5, 6, 45)
        r = d.decide(now, cron="0 6 * * *", max_delay="30", on_late="skip")
        self.assertFalse(r["run"])
        self.assertIsNone(r["warn"])
        r = d.decide(now, cron="0 6 * * *", max_delay="30", on_late="warn")
        self.assertTrue(r["run"])
        self.assertIn("45.0", r["warn"])
        r = d.decide(now, cron="0 6 * * *", max_delay="60", on_late="skip")
        self.assertTrue(r["run"])

    def test_nonnumeric_cron(self):
        with self.assertRaises(ValueError):
            d.scheduled_time(utc(2026, 1, 1), "*/5 * * * *")
        with self.assertRaises(ValueError):
            d.scheduled_time(utc(2026, 1, 1), "0 6 * *")

    def test_combined(self):
        # Right local hour but late: skipped. Wrong hour on time: skipped.
        r = d.decide(utc(2026, 7, 15, 8, 50), "America/Chicago", "3", "0 8 * * *", "30", "skip")
        self.assertFalse(r["run"])
        r = d.decide(utc(2026, 7, 15, 9, 0), "America/Chicago", "3", "0 9 * * *", "30", "skip")
        self.assertFalse(r["run"])
        r = d.decide(utc(2026, 7, 15, 8, 5), "America/Chicago", "3", "0 8 * * *", "30", "skip")
        self.assertTrue(r["run"])


@unittest.skipUnless(HAVE_TOOLS, "zdump/date not available")
class AgainstSystemTzdata(unittest.TestCase):
    def test_hourly_around_every_transition(self):
        checked = 0
        for zone in ZONES:
            for t in zdump_transitions(zone, 2024, 2027):
                for h in range(-30, 31):
                    when = t + timedelta(hours=h)
                    local_hour = d.dst_decision(when, zone, 0)[1]
                    py_hour = datetime.fromisoformat(local_hour).hour
                    sys_hour = system_local_hour(zone, when)
                    self.assertEqual(py_hour, sys_hour, (zone, when))
                    # The decision itself: run is true exactly when the system hour matches.
                    for target in (sys_hour, (sys_hour + 1) % 24):
                        run = d.dst_decision(when, zone, target)[0]
                        self.assertEqual(run, sys_hour == target, (zone, when, target))
                    checked += 1
        self.assertGreater(checked, 500)

    def test_two_cron_pattern_runs_once_a_day(self):
        # For each zone with DST, with the two UTC hours that cover the intended local hour in
        # winter and summer, exactly one of the two fires on every day of 2026 matches (never 0 or 2).
        for zone, hour in (("America/Chicago", 3), ("Europe/Berlin", 9), ("Australia/Sydney", 6)):
            offsets = {zone_off(zone, utc(2026, 1, 15)), zone_off(zone, utc(2026, 7, 15))}
            utc_hours = sorted({(hour - o) % 24 for o in offsets})
            self.assertEqual(len(utc_hours), 2, zone)
            day = utc(2026, 1, 1)
            while day.year == 2026:
                fired = 0
                for uh in utc_hours:
                    # both candidate crons on this UTC day; only count ones whose local date is unambiguous
                    now = day.replace(hour=uh)
                    if system_local_hour(zone, now) == hour:
                        fired += 1
                        self.assertTrue(d.dst_decision(now, zone, hour)[0])
                    else:
                        self.assertFalse(d.dst_decision(now, zone, hour)[0])
                self.assertIn(fired, (1,), (zone, day.date()))
                day += timedelta(days=1)


def zone_off(zone, when):
    local = datetime.fromisoformat(d.dst_decision(when, zone, 0)[1])
    return int(local.utcoffset().total_seconds() // 3600)


class MainEntry(unittest.TestCase):
    def run_main(self, env):
        out = tempfile.NamedTemporaryFile("r", delete=False)
        out.close()
        full = {"PATH": os.environ["PATH"], "GITHUB_OUTPUT": out.name}
        full.update(env)
        p = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "dst_cron.py")],
                           env=full, capture_output=True, text=True)
        with open(out.name) as fh:
            written = dict(l.rstrip("\n").split("=", 1) for l in fh if "=" in l)
        os.unlink(out.name)
        return p, written

    def test_outputs(self):
        p, w = self.run_main({"GITHUB_EVENT_NAME": "schedule", "PF_NOW": "2026-07-15T08:00:00Z",
                              "PF_TIMEZONE": "America/Chicago", "PF_LOCAL_HOUR": "3"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(w["run"], "true")
        self.assertTrue(w["local-time"].startswith("2026-07-15T03:00:00"))

    def test_manual_event_always_runs(self):
        p, w = self.run_main({"GITHUB_EVENT_NAME": "workflow_dispatch",
                              "PF_TIMEZONE": "America/Chicago", "PF_LOCAL_HOUR": "3"})
        self.assertEqual(w["run"], "true")

    def test_warn_annotation(self):
        p, w = self.run_main({"GITHUB_EVENT_NAME": "schedule", "PF_NOW": "2026-05-05T07:00:00Z",
                              "PF_SCHEDULE": "0 6 * * *", "PF_MAX_DELAY": "30", "PF_ON_LATE": "warn"})
        self.assertIn("::warning::", p.stdout)
        self.assertEqual(w["run"], "true")
        self.assertEqual(w["delay-minutes"], "60.0")

    def test_missing_event_name_defaults_to_schedule(self):
        p, w = self.run_main({"PF_NOW": "2026-01-15T08:00:00Z", "PF_TIMEZONE": "America/Chicago",
                              "PF_LOCAL_HOUR": "3"})
        self.assertEqual(w["run"], "false")

    def test_error_message_is_one_line(self):
        p, _ = self.run_main({"GITHUB_EVENT_NAME": "schedule", "PF_TIMEZONE": "Bad\n::set-output x=1",
                              "PF_LOCAL_HOUR": "3"})
        self.assertEqual(p.returncode, 1)
        self.assertEqual(len([l for l in p.stdout.splitlines() if l]), 1)

    def test_bad_zone_fails(self):
        p, _ = self.run_main({"GITHUB_EVENT_NAME": "schedule", "PF_TIMEZONE": "Nowhere/Land",
                              "PF_LOCAL_HOUR": "3"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("::error::", p.stdout)


if __name__ == "__main__":
    unittest.main()
