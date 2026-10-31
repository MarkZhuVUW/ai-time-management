"""Import conversation exports locally and count observable questioning patterns.

No text, title, original conversation ID, or original message ID is returned.
Frequency is observable; time spent and the user's actual task are not.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import heapq
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo
import zipfile


LOCAL_ZONE = ZoneInfo("Pacific/Auckland")
MAX_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000
SESSION_GAP = timedelta(minutes=20)
PRIVATE_SAMPLES_PER_STRATUM = 100
PRIVATE_EXCERPT_CHARACTERS = 2_000
CATEGORY_RULES = (
    ("sec_insiders", re.compile(r"\bform\s*4\b|\binsiders?\b|\b10b5-1\b|高管|内部人|內部人|\bsec\b.{0,30}\b(?:buy|sell|purchases?|sales?)\b", re.I)),
    ("powershop", re.compile(r"powershop|power\s*pack|特价包|特價包", re.I)),
    ("investing_events", re.compile(r"\bearnings\b|\b(?:revenue|profit)\s+guidance\b|\b(?:8-k|10-q|10-k)\b|财报|財報|业绩|業績|增发|增發|派息|分红|分紅", re.I)),
    ("other_finance", re.compile(r"\bstocks?\b|\bshares\b|\bportfolio\b|\betf\b|\bbitcoin\b|\bmortgage\b|股票|投资|投資|基金|利率|房贷|房貸|汇率|匯率|保险|保險", re.I)),
    ("software", re.compile(r"\b(?:python|javascript|typescript|coding|programming|debug|github|docker|sql|api|software|react|linux|git)\b|编程|編程|代码|代碼|调试|調試|写个程序|寫個程式|软件|軟件", re.I)),
    ("career_work", re.compile(r"\b(?:résumé|resume|cv|interview|salary|recruitment|promotion|colleague)\b|求职|求職|简历|簡歷|面试|面試|薪资|薪資|升职|升職|招聘|同事", re.I)),
    ("education_learning", re.compile(r"\b(?:course|exam|homework|university|learn|study|tutorial|translation|translate)\b|课程|課程|考试|考試|作业|作業|大学|大學|学习|學習|翻译|翻譯|语法|語法", re.I)),
    ("shopping", re.compile(r"\b(?:shopping|coupon|discount|warranty|refund|purchase|compare prices)\b|购物|購物|优惠券|優惠券|折扣|比价|比價|退货|退貨|保修|保養|哪个值得买|哪個值得買", re.I)),
    ("travel", re.compile(r"\b(?:travel|flight|itinerary|hotel|airport|airfare|tourism)\b|旅行|旅游|旅遊|机票|機票|航班|行程|酒店|机场|機場", re.I)),
    ("health", re.compile(r"\b(?:health|symptoms?|doctor|medication|workout|nutrition|fitness|dentist)\b|健康|症状|症狀|看病|医生|醫生|药物|藥物|健身|营养|營養|牙医|牙醫|减肥|減肥", re.I)),
    ("household_energy", re.compile(r"\b(?:electricity|utility bill|solar panel|home repair|plumber|renting|landlord)\b|电费|電費|水费|水費|能源|太阳能|太陽能|维修|維修|租房|房东|房東", re.I)),
    ("admin_legal", re.compile(r"\b(?:visa|immigration|passport|tax return|legal|contract|citizenship)\b|签证|簽證|移民|护照|護照|报税|報稅|法律|合同|公民|驾照|駕照", re.I)),
    ("productivity_planning", re.compile(r"\b(?:schedule|calendar|productivity|reminder|todo|time management|automati(?:on|c))\b|日程|日历|日曆|提醒|待办|待辦|时间管理|時間管理|效率|自动化|自動化", re.I)),
    ("food_cooking", re.compile(r"\b(?:recipe|cooking|meal|restaurant|grocery|groceries|baking)\b|食谱|食譜|做饭|做飯|烹饪|烹飪|餐厅|餐廳|菜谱|菜譜|买菜|買菜|烘焙", re.I)),
    ("media_entertainment", re.compile(r"\b(?:movie|cinema|gaming|videogame|music|podcast|television)\b|电影|電影|游戏|遊戲|音乐|音樂|播客|电视剧|電視劇", re.I)),
    ("relationships", re.compile(r"\b(?:relationship|parenting|childcare|marriage|friendship)\b|亲子|親子|育儿|育兒|婚姻|伴侣|伴侶|人际|人際|交友", re.I)),
    ("current_affairs", re.compile(r"\b(?:election|politics|geopolitics|current affairs)\b|选举|選舉|政治|地缘|地緣|国际新闻|國際新聞|时事|時事", re.I)),
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
                except (json.JSONDecodeError, RecursionError) as exc:
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
            if not isinstance(message, dict) or not isinstance(message.get("author"), dict) or not isinstance(message["author"].get("role"), str):
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


def _review_target(path: Path, sources: list[Path]) -> Path:
    # The non-resolved root is deliberate: a private/ symlink escaping the
    # repository must not become a new allowed root through resolution.
    private_root = Path.cwd().resolve() / "private"
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(private_root) or resolved == private_root:
        raise HistoryImportError("Private review output must remain under the current working directory's private/ directory, without symlink escape.")
    if resolved in {source.resolve() for source in sources}:
        raise HistoryImportError("Private review output must not overwrite a source export.")
    return resolved


def _source_records(paths: list[Path], quality: Counter, details: list[dict]):
    for index, path in enumerate(paths):
        payload, fmt = _load(path)
        records, conversation_count = _records(payload, fmt, quality)
        source = {"source_index": index, "format": fmt, "conversations_read": conversation_count, "input_user_records": 0, "unique_user_messages_added": 0, "duplicate_user_messages_skipped": 0}
        details.append(source)
        for record in records:
            yield index, record


def analyze_history(input_path: Path, as_of: datetime | None = None) -> dict:
    """Backward-compatible single-source wrapper for analyze_histories."""
    return analyze_histories([input_path], as_of)


def analyze_histories(input_paths: list[Path], as_of: datetime | None = None, *, private_review_path: Path | None = None) -> dict:
    """Return privacy-conscious statistics; never return source text or titles.

    ``as_of`` must be timezone-aware. Missing/invalid/future timestamps remain
    in overall user counts but are excluded from temporal statistics.
    Optional private excerpts are written separately under ./private/ only;
    they are never returned in this result.
    """
    if not input_paths:
        raise HistoryImportError("At least one source export is required.")
    paths = [Path(path) for path in input_paths]
    if len(paths) > 100:
        raise HistoryImportError("Import at most 100 export files per analysis.")
    review_target = _review_target(private_review_path, paths) if private_review_path is not None else None
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.astimezone(LOCAL_ZONE)
    quality = Counter({key: 0 for key in (
        "duplicate_user_messages_skipped", "missing_timestamps", "invalid_timestamps",
        "future_timestamps_excluded", "nontext_user_messages", "branch_nodes_excluded",
        "conversations_without_active_branch_pointer",
        "conflicting_duplicate_messages",
    )})
    source_details = []
    users = []
    dated = []
    seen = {}
    review_samples = defaultdict(list)
    review_stratum_counts = Counter()
    primary = Counter()
    multi = Counter()
    for source_index, record in _source_records(paths, quality, source_details):
        if record.get("role") != "user":
            continue
        source = source_details[source_index]
        source["input_user_records"] += 1
        key = (record["conversation_id"], record["message_id"])
        content = record.get("text", "")
        fingerprint = _hashed(json.dumps([content, record.get("timestamp")], ensure_ascii=False))
        if key in seen:
            quality["duplicate_user_messages_skipped"] += 1
            source["duplicate_user_messages_skipped"] += 1
            existing, prior_fingerprint = seen[key]
            if source_index not in existing["source_indexes"]:
                existing["source_indexes"].append(source_index)
            if fingerprint != prior_fingerprint:
                quality["conflicting_duplicate_messages"] += 1
            continue
        source["unique_user_messages_added"] += 1
        if not content.strip():
            quality["nontext_user_messages"] += 1
        labels = [name for name, pattern in CATEGORY_RULES if pattern.search(content)] or ["uncategorized"]
        category = labels[0]
        primary[category] += 1
        multi.update(labels)
        row = {"conversation_ref": _hashed(record["conversation_id"]), "message_ref": _hashed("\0".join(key)), "source_indexes": [source_index], "primary_category": category, "matched_categories": labels, "time": None}
        seen[key] = (row, fingerprint)
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
        if review_target is not None:
            recency = "undated" if moment is None else "recent_90_days" if moment.date() >= as_of.date() - timedelta(days=89) else "older"
            stratum = (category, recency)
            review_stratum_counts[stratum] += 1
            sample = {key: value for key, value in row.items() if key != "time"}
            sample.update({"timestamp_local": moment.isoformat() if moment else None, "recency": recency, "text_excerpt": content[:PRIVATE_EXCERPT_CHARACTERS], "text_truncated": len(content) > PRIVATE_EXCERPT_CHARACTERS})
            priority = int(row["message_ref"], 16)
            bucket = review_samples[stratum]
            entry = (-priority, row["message_ref"], sample)
            if len(bucket) < PRIVATE_SAMPLES_PER_STRATUM:
                heapq.heappush(bucket, entry)
            elif priority < -bucket[0][0]:
                heapq.heapreplace(bucket, entry)
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
    result = {
        "schema_version": 1,
        "timezone": "Pacific/Auckland",
        "status": status,
        "source": {"format": source_details[0]["format"] if len(paths) == 1 else "multiple_exports", "files_read": len(paths), "formats": dict(Counter(source["format"] for source in source_details)), "conversations_read": sum(source["conversations_read"] for source in source_details), "exports": source_details, "account_history_coverage": "unknown", "raw_text_included": False},
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
            "Topic labels are neutral coarse screening categories, not known interests or automatable workflows; uncategorized themes still require private review.",
            "Cross-export duplicates use conversation/message IDs; conflicting duplicate content keeps the first version and is disclosed in quality counters.",
        ],
    }
    if review_target is not None:
        samples = [entry[2] for bucket in review_samples.values() for entry in bucket]
        samples.sort(key=lambda item: (item["timestamp_local"] or "", item["message_ref"]))
        private_review = {
            "schema_version": 1,
            "privacy": "RAW PRIVATE QUESTION EXCERPTS: never commit, publish or include in public aggregate output.",
            "sampling": "all_questions" if len(samples) == len(users) else "deterministic_hash_sample_by_primary_category_and_recency",
            "population_user_messages": len(users), "sample_user_messages": len(samples),
            "max_per_category_recency_stratum": PRIVATE_SAMPLES_PER_STRATUM,
            "max_excerpt_characters": PRIVATE_EXCERPT_CHARACTERS,
            "strata": [{"primary_category": category, "recency": recency, "population": count, "sample": len(review_samples[(category, recency)])} for (category, recency), count in sorted(review_stratum_counts.items())],
            "questions": samples,
        }
        review_target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=review_target.parent, prefix=".review-", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(private_review, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, review_target)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        result["private_review"] = {"written": True, "sample_user_messages": len(samples), "sampling": private_review["sampling"], "raw_text_in_aggregate": False}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, nargs="+", help="One or more JSON, ZIP or JSONL exports; duplicates are unioned by IDs.")
    parser.add_argument("--output", type=Path, default=Path("private/history-analysis.json"))
    parser.add_argument("--as-of", help="Timezone-aware ISO 8601 timestamp for reproducible analysis.")
    parser.add_argument("--private-review", type=Path, help="Optional raw question excerpts; output must be inside ./private/ and is never embedded in the aggregate.")
    args = parser.parse_args()
    try:
        if args.output.resolve() in {path.resolve() for path in args.input}:
            raise HistoryImportError("Output must not overwrite the source export.")
        if args.private_review is not None and args.private_review.resolve() == args.output.resolve():
            raise HistoryImportError("Private review and aggregate output must be separate files.")
        moment = datetime.fromisoformat(args.as_of.replace("Z", "+00:00")) if args.as_of else None
        result = analyze_histories(args.input, moment, private_review_path=args.private_review)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (HistoryImportError, ValueError, OSError) as exc:
        parser.exit(2, f"Import failed: {exc}\n")
    print(f"Status: {result['status']}; user messages: {result['counts']['user_messages']}; dated: {result['counts']['dated_user_messages']}. Output saved locally.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
