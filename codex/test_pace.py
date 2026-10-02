import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pace


NOW = 1_000_000
HOUR = 3600


def window(used, left_hours, minutes=300):
    return {"used": used, "minutes": minutes, "reset": NOW + left_hours * HOUR}


class PacingTests(unittest.TestCase):
    def test_pause_rules(self):
        cases = [
            ([], None, None),
            ([window(50, 2)], None, None),
            ([window(90, 2)], None, None),
            ([window(98, .2)], None, None),
            ([window(98.1, .2)], "session-hard", NOW + .2 * HOUR),
            ([window(50, 84, 10080)], None, None),
            ([window(54, 84, 10080)], None, None),
            ([window(55, 84, 10080)], "weekly-lead", NOW + 4 * HOUR + 24 * 60),
            ([window(98, 1, 10080)], None, None),
            ([window(98.1, 84, 10080)], "weekly-hard", NOW + 84 * HOUR),
            ([window(98.1, 1), window(98.1, 84, 10080)], "weekly-hard", NOW + 84 * HOUR),
            ([window(98, -1)], None, None),
        ]
        for windows, kind, resume in cases:
            with self.subTest(windows=windows):
                result = pace.evaluate(windows, NOW)
                if kind is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(result[1], kind)
                    self.assertAlmostEqual(result[0], resume)

    def test_weekly_lead_starts_at_eight_hours_and_releases_at_four(self):
        w = window(55, 84, 10080)
        resume = pace.evaluate([w], NOW)[0]
        self.assertIsNone(pace.evaluate([w], resume))
        at_trigger = window(55, 168 * (1 - (55 / 100 - 8 / 168)), 10080)
        self.assertEqual(pace.evaluate([at_trigger], NOW)[1], "weekly-lead")
        self.assertIsNone(pace.evaluate([window(98, .5)], NOW))
        self.assertEqual(pace.evaluate([window(98.1, .5)], NOW)[1], "session-hard")

    def test_weekly_lead_is_not_shortened_by_session_reset(self):
        weekly = window(55, 84, 10080)
        session = window(50, 2)
        result = pace.evaluate([weekly, session], NOW)
        self.assertEqual(result[1], "weekly-lead")
        self.assertAlmostEqual(result[0], NOW + 4.4 * HOUR)

    def test_weekly_primary_is_not_a_session_window(self):
        buckets = pace.normalize({"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 55, "windowDurationMins": 10080, "resetsAt": NOW + 84 * HOUR}}})
        w = buckets["codex"]["windows"]
        self.assertEqual(pace.evaluate(w, NOW)[1], "weekly-lead")
        rendered = pace.render({"buckets": buckets}, now=NOW)
        self.assertIn("7D weekly [", rendered)
        self.assertIn("pace 50.0%", rendered)
        self.assertNotIn("5H session", rendered)

    def test_render_shows_every_reported_window(self):
        data = {"buckets": {"codex_bengalfox": {"windows": [
            window(40, 2, 300), window(60, 84, 10080)]}}}
        rendered = pace.render(data, bucket="codex_bengalfox", now=NOW)
        self.assertIn("7D weekly [", rendered)
        self.assertIn("used 60%", rendered)
        self.assertIn("5H session", rendered)

    def test_buckets_are_separate(self):
        source = {"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 99, "windowDurationMins": 10080, "resetsAt": NOW + HOUR}},
            "codex_bengalfox": {"primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": NOW + HOUR}},
        }}
        normalized = pace.normalize(source)
        self.assertEqual(normalized["codex"]["windows"][0]["used"], 99)
        self.assertEqual(pace.bucket_for("gpt-6-astra"), "codex")
        self.assertEqual(pace.bucket_for("gpt-5.6-luna"), "codex")
        self.assertEqual(pace.bucket_for("gpt-5.3-codex-spark"), "codex_bengalfox")

    def test_missing_invalid_expired_data_is_not_zero_usage(self):
        bad = {"rateLimits": {"primary": {"usedPercent": float("nan"),
               "windowDurationMins": 300, "resetsAt": NOW}}}
        self.assertEqual(pace.normalize(bad)["codex"]["windows"], [])
        rendered = pace.render({"buckets": {}}, now=NOW)
        self.assertIn("quota unavailable", rendered)
        expired = {"buckets": {"codex": {"windows": [window(98, -1, 10080)]}}}
        self.assertIn("reset passed", pace.render(expired, now=NOW))
        self.assertEqual(pace.ideal(window(0, 10), NOW), 0)

    def test_refresh_is_shared_and_failure_preserves_reading(self):
        response = {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 99, "windowDurationMins": 10080, "resetsAt": NOW + HOUR}}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pace, "rpc", return_value=response) as rpc:
            directory = Path(tmp)
            first = pace.snapshot(directory)
            second = pace.snapshot(directory)
            self.assertEqual(first, second)
            self.assertEqual(rpc.call_count, 1)
            rpc.side_effect = TimeoutError("test outage")
            failed = pace.snapshot(directory, force=True)
            self.assertEqual(failed["buckets"], first["buckets"])
            self.assertIn("test outage", failed["error"])
            self.assertEqual(pace.evaluate(failed["buckets"]["codex"]["windows"], NOW)[1], "weekly-hard")
            self.assertEqual(json.loads((directory / "limits.json").read_text()), failed)

    def test_gate_holds_and_releases_the_same_call(self):
        data = {"observed_at": NOW, "buckets": {"codex": {"windows": [window(99, 10 / HOUR)]}}}
        clock = [NOW]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds

        with tempfile.TemporaryDirectory() as tmp:
            def fresh(_):
                if clock[0] >= NOW+10:
                    return {"observed_at":clock[0], "buckets":{"codex":{"windows":[window(0,5)]}}}
                return data
            pace.gate(Path(tmp), {}, fresh, sleep, lambda: clock[0], lambda: clock[0])
            self.assertEqual(sum(sleeps), 10)
            self.assertIn("release", (Path(tmp) / "pace.log").read_text())

    def test_override_releases_already_waiting_hook(self):
        data = {"buckets": {"codex": {"windows": [window(99, 3)]}}}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            sleeps = []

            def sleep(seconds):
                sleeps.append(seconds)
                (directory / "override").touch()

            pace.gate(directory, {}, lambda _: data, sleep, lambda: NOW)
            self.assertEqual(len(sleeps), 1)

    def test_safety_deadline_denies_instead_of_releasing(self):
        data = {"buckets": {"codex": {"windows": [window(99, 3)]}}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(pace, "MAX_HOLD_SEC", 0):
            for event in ("PreToolUse", "UserPromptSubmit"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    pace.gate(Path(tmp), {"hook_event_name": event}, lambda _: data, wall=lambda: NOW)
                decision = json.loads(output.getvalue())
                if event == "PreToolUse":
                    self.assertEqual(decision["hookSpecificOutput"]["permissionDecision"], "deny")
                else:
                    self.assertFalse(decision["continue"])


if __name__ == "__main__":
    unittest.main()
