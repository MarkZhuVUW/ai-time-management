"""Synthetic fixtures only; test counts never describe the real user."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from audit.history import HistoryImportError, analyze_histories, analyze_history


AS_OF = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)


def export_conversation(messages, cid="synthetic-conversation"):
    mapping = {"root": {"parent": None, "message": None}}
    previous = "root"
    for mid, role, timestamp, text in messages:
        mapping[mid] = {"parent": previous, "message": {"id": mid, "author": {"role": role}, "create_time": timestamp, "content": {"content_type": "text", "parts": [text]}}}
        previous = mid
    return {"id": cid, "title": "DO NOT LEAK SYNTHETIC TITLE", "mapping": mapping, "current_node": previous}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def analyze(self, payload, name="conversations.json", as_of=AS_OF):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return analyze_history(path, as_of)

    def test_roles_privacy_and_category_conservation(self):
        fixture = [export_conversation([
            ("u1", "user", "2026-10-29T20:00:00Z", "SEC insider 股票 private-secret-123"),
            ("a1", "assistant", "2026-10-29T20:01:00Z", "Powershop"),
            ("u2", "user", "2026-10-29T20:04:00Z", "Powershop 特价包"),
        ])]
        result = self.analyze(fixture)
        self.assertEqual(result["counts"]["user_messages"], 2)
        self.assertEqual(result["categories"]["sec_insiders"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["other_finance"]["multilabel_user_messages"], 1)
        self.assertEqual(sum(row["primary_user_messages"] for row in result["categories"].values()), 2)
        serialized = json.dumps(result)
        self.assertNotIn("private-secret-123", serialized)
        self.assertNotIn("DO NOT LEAK", serialized)
        self.assertNotIn("synthetic-conversation", serialized)

    def test_active_branch_excludes_discarded_prompts(self):
        conversation = export_conversation([("kept", "user", "2026-10-01T00:00:00Z", "stocks")])
        conversation["mapping"]["discarded"] = {"parent": "root", "message": {"id": "discarded", "author": {"role": "user"}, "create_time": "2026-10-01T00:00:00Z", "content": {"parts": ["Powershop"]}}}
        result = self.analyze([conversation])
        self.assertEqual(result["counts"]["user_messages"], 1)
        self.assertEqual(result["quality"]["branch_nodes_excluded"], 1)
        self.assertEqual(result["categories"]["powershop"]["primary_user_messages"], 0)

    def test_no_active_pointer_is_disclosed(self):
        conversation = export_conversation([("u", "user", None, "hello")])
        del conversation["current_node"]
        result = self.analyze([conversation])
        self.assertEqual(result["quality"]["conversations_without_active_branch_pointer"], 1)

    def test_invalid_parent_chain_fails_explicitly(self):
        conversation = export_conversation([("u", "user", None, "hello")])
        conversation["mapping"]["u"]["parent"] = "u"
        with self.assertRaises(HistoryImportError):
            self.analyze([conversation])

    def test_duplicate_conversation_exports_do_not_double_count(self):
        conversation = export_conversation([("u", "user", "2026-10-01T00:00:00Z", "hello")])
        result = self.analyze([conversation, conversation])
        self.assertEqual(result["counts"]["user_messages"], 1)
        self.assertEqual(result["quality"]["duplicate_user_messages_skipped"], 1)

    def test_multiple_json_and_zip_union_deduplicates_and_retains_sources(self):
        original = export_conversation([("u1", "user", "2026-10-01T00:00:00Z", "hello")])
        newer = export_conversation([
            ("u1", "user", "2026-10-01T00:00:00Z", "hello"),
            ("u2", "user", "2026-10-02T00:00:00Z", "new prompt"),
        ])
        first = self.root / "personal.json"
        second = self.root / "work.zip"
        first.write_text(json.dumps([original]), encoding="utf-8")
        with zipfile.ZipFile(second, "w") as archive:
            archive.writestr("conversations.json", json.dumps([newer]))
        result = analyze_histories([first, second], AS_OF)
        self.assertEqual(result["counts"]["user_messages"], 2)
        self.assertEqual(result["source"]["files_read"], 2)
        self.assertEqual(result["source"]["format"], "multiple_exports")
        self.assertEqual(result["source"]["exports"][0]["unique_user_messages_added"], 1)
        self.assertEqual(result["source"]["exports"][1]["duplicate_user_messages_skipped"], 1)
        self.assertEqual(result["evidence_index"][0]["source_indexes"], [0, 1])
        self.assertEqual(result["evidence_index"][1]["source_indexes"], [1])
        self.assertNotIn("personal.json", json.dumps(result))

    def test_conflicting_duplicate_versions_are_disclosed(self):
        paths = [self.root / "one.json", self.root / "two.json"]
        for path, text in zip(paths, ("old text", "changed text")):
            path.write_text(json.dumps([export_conversation([("u", "user", None, text)])]), encoding="utf-8")
        result = analyze_histories(paths, AS_OF)
        self.assertEqual(result["counts"]["user_messages"], 1)
        self.assertEqual(result["quality"]["conflicting_duplicate_messages"], 1)

    def test_empty_source_list_is_not_empty_history(self):
        with self.assertRaises(HistoryImportError):
            analyze_histories([], AS_OF)

    def test_neutral_taxonomy_covers_unrelated_domains_and_conserves_counts(self):
        result = self.analyze([export_conversation([
            ("u1", "user", None, "Plan a travel itinerary and hotel"),
            ("u2", "user", None, "How do I debug Python?"),
            ("u3", "user", None, "Need doctor for symptoms"),
            ("u4", "user", None, "电费水费怎么结算"),
            ("u5", "user", None, "Which visa for immigration?"),
        ])])
        self.assertGreaterEqual(len(result["categories"]), 15)
        self.assertEqual(result["categories"]["travel"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["software"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["health"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["household_energy"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["powershop"]["primary_user_messages"], 0)
        self.assertEqual(sum(category["primary_user_messages"] for category in result["categories"].values()), 5)

    def test_sec_seconds_not_regulatory_insider_signal(self):
        result = self.analyze([export_conversation([
            ("u1", "user", None, "wait 4 sec before retry"),
            ("u2", "user", None, "How to inspect form4 insider purchases?"),
        ])])
        self.assertEqual(result["categories"]["sec_insiders"]["primary_user_messages"], 1)

    def test_generic_share_and_guidance_do_not_invent_finance_interest(self):
        result = self.analyze([export_conversation([
            ("u1", "user", None, "Please share Python code"),
            ("u2", "user", None, "career guidance for interview"),
        ])])
        self.assertEqual(result["categories"]["software"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["career_work"]["primary_user_messages"], 1)
        self.assertEqual(result["categories"]["investing_events"]["primary_user_messages"], 0)
        self.assertEqual(result["categories"]["other_finance"]["primary_user_messages"], 0)

    def test_private_review_is_separate_private_and_deduplicated(self):
        path = self.root / "export.json"
        path.write_text(json.dumps([export_conversation([("u", "user", "2026-10-01T00:00:00Z", "UNIQUE PRIVATE QUESTION")])]), encoding="utf-8")
        output = self.root / "private" / "review.json"
        with patch("audit.history.Path.cwd", return_value=self.root):
            result = analyze_histories([path, path], AS_OF, private_review_path=output)
        self.assertNotIn("UNIQUE PRIVATE QUESTION", json.dumps(result))
        review = json.loads(output.read_text())
        self.assertEqual(review["questions"][0]["text_excerpt"], "UNIQUE PRIVATE QUESTION")
        self.assertEqual(review["questions"][0]["source_indexes"], [0, 1])
        self.assertEqual(review["sample_user_messages"], 1)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_private_review_rejects_public_path_and_symlink_escape(self):
        path = self.root / "export.json"
        path.write_text("[]", encoding="utf-8")
        with patch("audit.history.Path.cwd", return_value=self.root):
            with self.assertRaises(HistoryImportError):
                analyze_histories([path], AS_OF, private_review_path=self.root / "public.json")
            outside = self.root / "elsewhere"
            outside.mkdir()
            (self.root / "private").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(HistoryImportError):
                analyze_histories([path], AS_OF, private_review_path=self.root / "private" / "leak.json")

    def test_private_review_is_bounded_stratified_sample(self):
        path = self.root / "export.json"
        messages = [(f"old-{i}", "user", "2026-01-01T00:00:00Z", "old unrelated question") for i in range(8)]
        messages += [(f"new-{i}", "user", "2026-10-01T00:00:00Z", "travel hotel " + "x" * 60) for i in range(8)]
        path.write_text(json.dumps([export_conversation(messages)]), encoding="utf-8")
        output = self.root / "private" / "review.json"
        with patch("audit.history.Path.cwd", return_value=self.root), patch("audit.history.PRIVATE_SAMPLES_PER_STRATUM", 2), patch("audit.history.PRIVATE_EXCERPT_CHARACTERS", 24):
            result = analyze_histories([path], AS_OF, private_review_path=output)
        review = json.loads(output.read_text())
        self.assertEqual(result["counts"]["user_messages"], 16)
        self.assertEqual(review["sample_user_messages"], 4)
        self.assertEqual({row["recency"] for row in review["questions"]}, {"older", "recent_90_days"})
        self.assertTrue(all(len(row["text_excerpt"]) <= 24 for row in review["questions"]))
        self.assertEqual(review["sampling"], "deterministic_hash_sample_by_primary_category_and_recency")

    def test_missing_invalid_and_future_dates_excluded_from_rates(self):
        result = self.analyze([export_conversation([
            ("missing", "user", None, "hello"),
            ("invalid", "user", "not a date", "hello"),
            ("naive", "user", "2026-10-31T00:00:00", "hello"),
            ("future", "user", "2027-01-01T00:00:00Z", "hello"),
            ("valid", "user", "2026-10-31T00:00:00Z", "hello"),
        ])])
        self.assertEqual(result["counts"]["user_messages"], 5)
        self.assertEqual(result["counts"]["dated_user_messages"], 1)
        self.assertEqual(result["quality"]["missing_timestamps"], 1)
        self.assertEqual(result["quality"]["invalid_timestamps"], 2)
        self.assertEqual(result["quality"]["future_timestamps_excluded"], 1)

    def test_empty_and_undated_inputs_do_not_claim_analyzed(self):
        empty = self.analyze([])
        self.assertEqual(empty["status"], "no_user_messages")
        self.assertIsNone(empty["metrics"]["prompts_per_calendar_week"])
        undated = self.analyze([export_conversation([("u", "user", None, "hello")])])
        self.assertEqual(undated["status"], "no_dated_user_messages")

    def test_calendar_denominator_includes_zero_days(self):
        as_of = datetime(2026, 10, 31, 0, tzinfo=timezone.utc)
        result = self.analyze([export_conversation([("u", "user", "2026-10-25T00:00:00Z", "hello")])], as_of=as_of)
        self.assertEqual(result["window"]["calendar_days"], 7)
        self.assertEqual(result["window"]["zero_message_days"], 6)
        self.assertEqual(result["metrics"]["prompts_per_calendar_week"], 1)

    def test_auckland_local_date_and_iso_week(self):
        result = self.analyze([export_conversation([
            ("u1", "user", "2026-10-25T11:30:00Z", "hello"),
            ("u2", "user", "2026-10-25T12:00:00Z", "hello"),
        ])])
        self.assertEqual(result["metrics"]["daily_counts"], {"2026-10-26": 2})
        self.assertEqual(result["metrics"]["weekly_counts"], {"2026-W44": 2})

    def test_session_gap_respects_elapsed_time_across_dst(self):
        # NZ spring-forward: local 01:55 -> 03:10 is only 15 elapsed minutes.
        result = self.analyze([export_conversation([
            ("u1", "user", "2026-09-26T13:55:00Z", "hello"),
            ("u2", "user", "2026-09-26T14:10:00Z", "hello"),
            ("u3", "user", "2026-09-26T14:31:00Z", "hello"),
        ])])
        self.assertEqual(result["metrics"]["inferred_sessions"], 2)

    def test_recent90_and_older_have_separate_calendar_rates(self):
        result = self.analyze([export_conversation([
            ("old", "user", "2026-01-01T00:00:00Z", "hello"),
            ("recent", "user", "2026-10-31T00:00:00Z", "hello"),
        ])])
        self.assertEqual(result["recent_90_days"]["calendar_days"], 90)
        self.assertEqual(result["recent_90_days"]["user_messages"], 1)
        self.assertEqual(result["older"]["user_messages"], 1)
        self.assertEqual(result["older"]["calendar_days"] + 90, result["window"]["calendar_days"])

    def test_zip_is_read_without_extraction(self):
        path = self.root / "export.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("account/conversations.json", json.dumps([export_conversation([("u", "user", None, "hello")])]))
            archive.writestr("other.txt", "do not extract")
        result = analyze_history(path, AS_OF)
        self.assertEqual(result["source"]["format"], "chatgpt_zip")
        self.assertEqual(result["counts"]["user_messages"], 1)
        self.assertFalse((self.root / "account").exists())

    def test_unsafe_and_ambiguous_zip_fail(self):
        for name, entries in (
            ("unsafe.zip", ["../conversations.json"]),
            ("ambiguous.zip", ["a/conversations.json", "b/conversations.json"]),
        ):
            path = self.root / name
            with zipfile.ZipFile(path, "w") as archive:
                for entry in entries:
                    archive.writestr(entry, "[]")
            with self.assertRaises(HistoryImportError):
                analyze_history(path, AS_OF)

    def test_zip_uncompressed_limit_is_enforced(self):
        path = self.root / "export.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("conversations.json", " " * 512)
        with patch("audit.history.MAX_BYTES", 128):
            with self.assertRaises(HistoryImportError):
                analyze_history(path, AS_OF)

    def test_normalized_jsonl(self):
        path = self.root / "normalized.jsonl"
        rows = [
            {"conversation_id": "c", "message_id": "u", "role": "user", "timestamp": "2026-10-31T00:00:00Z", "text": "hello"},
            {"conversation_id": "c", "message_id": "a", "role": "assistant", "text": "hi"},
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        result = analyze_history(path, AS_OF)
        self.assertEqual(result["counts"]["user_messages"], 1)

    def test_unknown_shape_fails_instead_of_being_empty(self):
        for payload in ({"messages": []}, [{"random": "shape"}]):
            with self.assertRaises(HistoryImportError):
                self.analyze(payload)

    def test_nontext_messages_count_without_content_inference(self):
        fixture = export_conversation([("u", "user", None, "")])
        fixture["mapping"]["u"]["message"]["content"] = {"parts": [{"content_type": "image_asset_pointer", "asset_pointer": "secret-path"}]}
        result = self.analyze([fixture])
        self.assertEqual(result["quality"]["nontext_user_messages"], 1)
        self.assertEqual(result["categories"]["uncategorized"]["primary_user_messages"], 1)
        self.assertNotIn("secret-path", json.dumps(result))

    def test_cli_writes_local_result_and_returns_nonzero_on_bad_input(self):
        path = self.root / "export.json"
        path.write_text("[]", encoding="utf-8")
        output = self.root / "private" / "stats.json"
        proc = subprocess.run([sys.executable, "-m", "audit.history", str(path), "--output", str(output), "--as-of", AS_OF.isoformat()], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(output.read_text())["status"], "no_user_messages")
        path.write_text("not json", encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "audit.history", str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)

    def test_cli_refuses_source_overwrite(self):
        path = self.root / "export.json"
        original = "[]"
        path.write_text(original, encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "audit.history", str(path), "--output", str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(path.read_text(), original)

    def test_cli_accepts_multiple_sources_and_protects_each_source(self):
        paths = [self.root / "one.json", self.root / "two.json"]
        for path in paths:
            path.write_text("[]", encoding="utf-8")
        output = self.root / "aggregate.json"
        proc = subprocess.run([sys.executable, "-m", "audit.history", *(str(path) for path in paths), "--output", str(output)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(output.read_text())["source"]["files_read"], 2)
        proc = subprocess.run([sys.executable, "-m", "audit.history", *(str(path) for path in paths), "--output", str(paths[1])], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(paths[1].read_text(), "[]")


if __name__ == "__main__":
    unittest.main()
