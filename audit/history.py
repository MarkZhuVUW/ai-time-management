"""Import conversation exports locally and count observable questioning patterns.

No text, title, original conversation ID, or original message ID is returned.
Frequency is observable; time spent and the user's actual task are not.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from zoneinfo import ZoneInfo
import zipfile


LOCAL_ZONE = ZoneInfo("Pacific/Auckland")
MAX_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
SESSION_GAP = timedelta(minutes=20)
CATEGORY_RULES = (
    ("sec_insiders", re.compile(r"\bsec\b|\bform\s*4\b|insider|高管|内部人", re.I)),
    ("powershop", re.compile(r"powershop|power\s*pack|电费|電費|特价包|特價包", re.I)),
    ("investing_events", re.compile(r"\bearnings\b|\bguidance\b|\b(?:8-k|10-q|10-k)\b|财报|財報|业绩|業績|增发|增發|派息|分红|分紅", re.I)),
    ("other_finance", re.compile(r"\bstocks?\b|\bshares?\b|\bportfolio\b|\betf\b|\bbitcoin\b|\bmortgage\b|股票|投资|投資|基金|利率|房贷|房貸|汇率|匯率|保险|保險", re.I)),
)
CATEGORY_NAMES = tuple(name for name, _ in CATEGORY_RULES) + ("uncategorized",)


class HistoryImportError(ValueError):
    """The input cannot be safely or unambiguously interpreted."""


def _hashed(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise HistoryImportError("Invalid UTF-8 JSON; input contents are not echoed.") from exc


def _load(path: Path) -> tuple[Any, str]:
    if not path.is_file():
        raise HistoryImportError("Input is not a readable file.")
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise HistoryImportError("Input exceeds the 512 MiB archive size limit.")
    if zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                if len(archive.infolist()) > MAX_ZIP_ENTRIES:
                    raise HistoryImportError("ZIP exceeds the 10,000 entry limit.")
                candidates = []
                for entry in archive.infolist():
                    pure = PurePosixPath(entry.filename.replace("\\", "/"))
                    if pure.is_absolute() or ".." in pure.parts or (pure.parts and ":" in pure.parts[0]):
                        raise HistoryImportError("ZIP contains unsafe paths; no entries were extracted.")
                    if pure.name == "conversations.json" and not entry.is_dir():
                        candidates.append(entry)
                if len(candidates) != 1:
                    raise HistoryImportError("ZIP must contain exactly one conversations.json; combine workspace exports separately.")
                entry = candidates[0]
                if entry.file_size > MAX_BYTES:
                    raise HistoryImportError("Conversation JSON exceeds the 256 MiB input limit.")
                if entry.flag_bits & 1:
                    raise HistoryImportError("Encrypted ZIP is unsupported; provide a locally decrypted export.")
                with archive.open(entry) as handle:
                    data = handle.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise HistoryImportError("Conversation JSON exceeds the 256 MiB input limit.")
                return _json(data), "chatgpt_zip"
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise HistoryImportError("ZIP could not be read safely.") from exc
    if path.stat().st_size > MAX_BYTES:
        raise HistoryImportError("Input exceeds the 256 MiB input limit.")
    data = path.read_bytes()
    if path.suffix.lower() == ".jsonl":
        result = []
        try:
            for number, line in enumerate(data.decode("utf-8-sig").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise HistoryImportError(f"Invalid JSONL at line {number}; contents are not echoed.") from exc
        except UnicodeDecodeError as exc:
            raise HistoryImportError("JSONL must use UTF-8 encoding.") from exc
        return result, "normalized_jsonl"
    return _json(data), "chatgpt_json"


def _timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean timestamp")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).astimezone(LOCAL_ZONE)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp needs a timezone")
        return parsed.astimezone(LOCAL_ZONE)
    raise ValueError("Unsupported timestamp")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, dict):
        return ""
    if isinstance(content.get("parts"), list):
        return "\n".join(part for part in content["parts"] if isinstance(part, str))
    return content.get("text", "") if isinstance(content.get("text"), str) else ""


def _records(payload: Any, fmt: str, quality: Counter) -> tuple[list[dict], int]:
    if fmt == "normalized_jsonl":
        if not isinstance(payload, list):
            raise HistoryImportError("JSONL input must contain message objects.")
        result = []
        conversations = set()
        for index, row in enumerate(payload, 1):
            if not isinstance(row, dict) or not all(key in row for key in ("conversation_id", "message_id", "role", "text")):
                raise HistoryImportError(f"JSONL row {index} needs conversation_id, message_id, role and text.")
            if not isinstance(row["conversation_id"], str) or not row["conversation_id"] or not isinstance(row["message_id"], str) or not row["message_id"]:
                raise HistoryImportError(f"JSONL row {index} needs nonempty string IDs.")
            if not isinstance(row["role"], str) or not isinstance(row["text"], str):
                raise HistoryImportError(f"JSONL row {index} role and text must be strings.")
            conversations.add(row["conversation_id"])
            result.append(dict(row))
        return result, len(conversations)
    if isinstance(payload, dict) and "conversations" in payload:
        payload = payload["conversations"]
    if not isinstance(payload, list):
        raise HistoryImportError("Expected a ChatGPT conversation list or an object with a conversations list.")
    result = []
    for index, conversation in enumerate(payload, 1):
        if not isinstance(conversation, dict) or not isinstance(conversation.get("mapping"), dict):
            raise HistoryImportError(f"Conversation {index} needs a ChatGPT mapping object; unknown formats are not treated as empty history.")
        cid = conversation.get("id") or conversation.get("conversation_id")
        if not isinstance(cid, str) or not cid:
            raise HistoryImportError(f"Conversation {index} needs a nonempty string ID.")
        mapping = conversation["mapping"]
        current_node = conversation.get("current_node")
        if current_node is not None:
            nodes = []
            visited = set()
            node_id = current_node
            while node_id is not None:
                if not isinstance(node_id, str) or node_id in visited or node_id not in mapping:
                    raise HistoryImportError(f"Conversation {index} has an invalid current_node/parent chain.")
                visited.add(node_id)
                node = mapping[node_id]
                if not isinstance(node, dict):
                    raise HistoryImportError(f"Conversation {index} has an invalid mapping node.")
                nodes.append((node_id, node))
                node_id = node.get("parent")
            quality["branch_nodes_excluded"] += len(mapping) - len(visited)
            nodes.reverse()
        else:
            nodes = list(mapping.items())
            quality["conversations_without_active_branch_pointer"] += 1
        for node_id, node in nodes:
            if not isinstance(node, dict):
                raise HistoryImportError(f"Conversation {index} has an invalid mapping node.")
            message = node.get("message")
            if message is None:
                continue
            if not isinstance(message, dict) or not isinstance(message.get("author"), dict):
                raise HistoryImportError(f"Conversation {index} has an unsupported message shape.")
            mid = message.get("id") or node_id
            if not isinstance(mid, str) or not mid:
                raise HistoryImportError(f"Conversation {index} has a message without a usable ID.")
            result.append({
                "conversation_id": cid,
                "message_id": mid,
                "role": message["author"].get("role"),
                "text": _text(message.get("content")),
                "timestamp": message.get("create_time"),
            })
    return result, len(payload)


def _sessions(rows: list[dict]) -> int:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["conversation_ref"]].append(row["time"])
    count = 0
    for moments in grouped.values():
        moments = [moment.astimezone(timezone.utc) for moment in moments]
        moments.sort()
        previous = None
        for moment in moments:
            if previous is None or moment - previous > SESSION_GAP:
                count += 1
            previous = moment
    return count


def _period(rows: list[dict], start: date | None, end: date) -> dict:
    if start is None or start > end:
        return {"start_date": None, "end_date": end.isoformat(), "calendar_days": 0, "user_messages": 0, "prompts_per_calendar_week": None}
    count = sum(start <= row["time"].date() <= end for row in rows)
    days = (end - start).days + 1
    return {"start_date": start.isoformat(), "end_date": end.isoformat(), "calendar_days": days, "user_messages": count, "prompts_per_calendar_week": round(count * 7 / days, 4)}


def analyze_history(input_path: Path, as_of: datetime | None = None) -> dict:
    """Return privacy-conscious statistics; never return source text or titles.

    ``as_of`` must be timezone-aware. Missing/invalid/future timestamps remain
    in overall user counts but are excluded from temporal statistics.
    """
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(LOCAL_ZONE)
    quality = Counter({key: 0 for key in (
        "duplicate_user_messages_skipped", "missing_timestamps", "invalid_timestamps",
        "future_timestamps_excluded", "nontext_user_messages", "branch_nodes_excluded",
        "conversations_without_active_branch_pointer",
    )})
    payload, fmt = _load(Path(input_path))
    records, conversation_count = _records(payload, fmt, quality)
    users = []
    dated = []
    seen = set()
    primary = Counter()
    multi = Counter()
    for record in records:
        if record.get("role") != "user":
            continue
        key = (record["conversation_id"], record["message_id"])
        if key in seen:
            quality["duplicate_user_messages_skipped"] += 1
            continue
        seen.add(key)
        content = record.get("text", "")
        if not content.strip():
            quality["nontext_user_messages"] += 1
        labels = [name for name, pattern in CATEGORY_RULES if pattern.search(content)] or ["uncategorized"]
        category = labels[0]
        primary[category] += 1
        multi.update(labels)
        row = {"conversation_ref": _hashed(record["conversation_id"]), "message_ref": _hashed("\0".join(key)), "primary_category": category, "matched_categories": labels, "time": None}
        value = record.get("timestamp")
        try:
            moment = _timestamp(value)
        except (ValueError, OverflowError, OSError):
            quality["invalid_timestamps"] += 1
            moment = None
        if value is None or value == "":
            quality["missing_timestamps"] += 1
        if moment and moment > as_of:
            quality["future_timestamps_excluded"] += 1
            moment = None
        row["time"] = moment
        users.append(row)
        if moment:
            dated.append(row)
    dated.sort(key=lambda row: (row["time"].astimezone(timezone.utc), row["conversation_ref"], row["message_ref"]))
    daily = Counter(row["time"].date().isoformat() for row in dated)
    weekly = Counter(f"{row['time'].isocalendar().year}-W{row['time'].isocalendar().week:02d}" for row in dated)
    first = dated[0]["time"].date() if dated else None
    last = dated[-1]["time"].date() if dated else None
    whole = _period(dated, first, as_of.date())
    recent_boundary = as_of.date() - timedelta(days=89)
    recent_start = max(first, recent_boundary) if first else None
    old_end = recent_boundary - timedelta(days=1)
    older_start = first if first and first <= old_end else None
    categories = {}
    for category in CATEGORY_NAMES:
        category_rows = [row for row in dated if row["primary_category"] == category]
        categories[category] = {
            "primary_user_messages": primary[category],
            "primary_dated_messages": len(category_rows),
            "multilabel_user_messages": multi[category],
            "inferred_sessions": _sessions(category_rows),
            "prompts_per_calendar_week": round(len(category_rows) * 7 / whole["calendar_days"], 4) if whole["calendar_days"] else None,
        }
    status = "analyzed" if dated else "no_dated_user_messages" if users else "no_user_messages"
    return {
        "schema_version": 1,
        "timezone": "Pacific/Auckland",
        "status": status,
        "source": {"format": fmt, "conversations_read": conversation_count, "account_history_coverage": "unknown", "raw_text_included": False},
        "quality": dict(quality),
        "counts": {"user_messages": len(users), "dated_user_messages": len(dated), "conversations_with_user_messages": len({row["conversation_ref"] for row in users})},
        "window": {"first_date": first.isoformat() if first else None, "last_date": last.isoformat() if last else None, "as_of_date": as_of.date().isoformat(), "calendar_days": whole["calendar_days"], "zero_message_days": whole["calendar_days"] - len(daily)},
        "metrics": {"daily_counts": dict(sorted(daily.items())), "weekly_counts": dict(sorted(weekly.items())), "max_day": max(daily.values(), default=0), "max_week": max(weekly.values(), default=0), "active_days": len(daily), "active_weeks": len(weekly), "prompts_per_calendar_week": whole["prompts_per_calendar_week"], "inferred_sessions": _sessions(dated), "session_gap_minutes": 20},
        "categories": categories,
        "recent_90_days": _period(dated, recent_start, as_of.date()),
        "older": _period(dated, older_start, old_end),
        "evidence_index": [{**{key: value for key, value in row.items() if key != "time"}, "timestamp_local": row["time"].isoformat() if row["time"] else None} for row in users],
        "limitations": [
            "Counts apply only to supplied exports, not necessarily all account/chat/work history.",
            "Keywords are a screening tool, not proof of intent, task identity or automatable work.",
            "Primary categories conserve counts; multilabel totals overlap and must not be added.",
            "Twenty-minute conversation sessions are a heuristic, not measured work duration or distinct real-world tasks.",
            "Timestamps cannot measure reading, research, waiting or time saved; missing and future dates are excluded from temporal metrics.",
            "Calendar rates include zero-message days from the first dated prompt through as_of; coverage of those days is not verified.",
            "Without current_node all mapping nodes are counted; unresolved branches may inflate counts.",
            "Image/audio-only user messages count as user messages but their content is not classified.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("private/history-analysis.json"))
    parser.add_argument("--as-of", help="Timezone-aware ISO 8601 timestamp for reproducible analysis.")
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve():
            raise HistoryImportError("Output must not overwrite the source export.")
        moment = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
        result = analyze_history(args.input, moment)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (HistoryImportError, ValueError, OSError) as exc:
        parser.exit(2, f"Import failed: {exc}\n")
    print(f"Status: {result['status']}; user messages: {result['counts']['user_messages']}; dated: {result['counts']['dated_user_messages']}. Output saved locally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
