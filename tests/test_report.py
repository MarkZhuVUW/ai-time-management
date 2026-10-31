"""Integration checks use synthetic exports in temporary dirs, never real chats."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone

from audit.history import analyze_history
from audit.report import ROOT, generate


class ReportTests(unittest.TestCase):
    def test_no_history_keeps_facts_unknown_and_labels_all_scenarios(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            generate(ROOT / "data/scenarios.json", out)
            summary = json.loads((out / "summary.json").read_text())
            self.assertFalse(summary["history"]["loaded"])
            self.assertIsNone(summary["history"]["counts"])
            self.assertEqual(summary["scenario_status"], "illustrative_assumptions_not_user_measurements")
            self.assertNotIn("total_savings", summary)
            for name in ("REPORT.zh-CN.md", "scenario-savings.svg", "scenario-savings.png", "calculator.html", "scenarios.csv"):
                self.assertGreater((out / name).stat().st_size, 100)
            self.assertFalse((out / "observed-topic-counts.svg").exists())

    def test_supplied_history_keeps_raw_text_and_message_index_private(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.jsonl"
            source.write_text(json.dumps({"conversation_id": "private-conversation", "message_id": "private-message",
                                          "role": "user", "text": "Powershop do-not-publish-this-raw-text",
                                          "timestamp": "2026-10-30T12:00:00Z"}) + "\n")
            history = analyze_history(source, datetime(2026, 10, 31, 9, tzinfo=timezone.utc))
            history_path = root / "history.json"
            history_path.write_text(json.dumps(history))
            out = root / "reports"
            generate(ROOT / "data/scenarios.json", out, history_path)
            summary = json.loads((out / "summary.json").read_text())
            self.assertEqual(summary["history"]["counts"]["user_messages"], 1)
            self.assertNotIn("evidence_index", summary["history"])
            self.assertNotIn("daily_counts", summary["history"]["metrics"])
            self.assertTrue((out / "observed-topic-counts.svg").exists())
            for file in out.iterdir():
                if file.suffix == ".png":
                    continue
                text = file.read_text(encoding="utf-8-sig")
                self.assertNotIn("do-not-publish-this-raw-text", text)
                self.assertNotIn("private-conversation", text)
                self.assertNotIn(history["evidence_index"][0]["message_ref"], text)
            generate(ROOT / "data/scenarios.json", out)
            self.assertFalse((out / "observed-topic-counts.svg").exists())

    def test_invalid_analysis_is_rejected_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / "raw.json"
            raw.write_text("[]")
            with self.assertRaises(ValueError):
                generate(ROOT / "data/scenarios.json", root / "reports", raw)
            self.assertFalse((root / "reports").exists())

    @unittest.skipUnless(shutil.which("node"), "Node available to verify offline calculator script syntax")
    def test_interactive_calculator_script_parses(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            generate(ROOT / "data/scenarios.json", out)
            page = (out / "calculator.html").read_text()
            script = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
            result = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("fetch(", script)


if __name__ == "__main__":
    unittest.main()
