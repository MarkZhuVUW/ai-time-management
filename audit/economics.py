"""Transparent scenario calculation; no input here is a measured user frequency."""

from __future__ import annotations

import json
import math
from pathlib import Path


def net_weekly_minutes(candidate: dict, scenario: str) -> float:
    """Only count covered incremental checks, less maintenance and exceptions."""
    frequency = candidate["checks_per_week"][scenario]
    fraction = candidate["automation_fraction"][scenario]
    manual = candidate["manual_minutes_per_check"]
    review = candidate["review_minutes_per_check"]
    maintenance = candidate["maintenance_minutes_per_week"]
    exceptions = candidate["exception_minutes_per_week"]
    for name, value in {"frequency": frequency, "manual": manual,
                        "review": review, "maintenance": maintenance,
                        "exceptions": exceptions,
                        "build": candidate["build_minutes"]["central"]}.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite nonnegative number")
    if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or not 0 <= fraction <= 1:
        raise ValueError("automation_fraction must be between 0 and 1")
    overhead = maintenance + exceptions
    return frequency * fraction * (manual - review) - overhead


def evaluate(candidate: dict) -> dict:
    savings = {s: round(net_weekly_minutes(candidate, s), 2)
               for s in ("low", "central", "high")}
    central = savings["central"]
    delta = candidate["manual_minutes_per_check"] - candidate["review_minutes_per_check"]
    coverage = candidate["automation_fraction"]["central"]
    overhead = candidate["maintenance_minutes_per_week"] + candidate["exception_minutes_per_week"]
    return {
        "id": candidate["id"], "name_zh": candidate["name_zh"],
        "status": "illustrative_assumptions_not_user_measurements",
        "net_minutes_per_week": savings,
        "build_minutes_central": candidate["build_minutes"]["central"],
        "central_payback_weeks": round(candidate["build_minutes"]["central"] / central, 1) if central > 0 else None,
        "central_annual_net_hours_after_build": round((central * 52 - candidate["build_minutes"]["central"]) / 60, 2),
        "weekly_checks_to_break_even_within_12_weeks": round((overhead + candidate["build_minutes"]["central"] / 12) / (coverage * delta), 2) if coverage > 0 and delta > 0 else None,
        "validation_gate": candidate["validation_gate"],
    }


def load_scenarios(path: Path) -> tuple[dict, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("status") != "illustrative_assumptions_not_user_measurements":
        raise ValueError("Scenario file must explicitly identify unmeasured assumptions")
    return data, [evaluate(c) for c in data["candidates"]]
