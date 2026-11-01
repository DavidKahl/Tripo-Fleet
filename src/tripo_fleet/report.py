"""Summarise all attempts into numbers the video can quote: time, credits, triangles, size."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .runner import Workspace, read_state


def _seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))  # noqa: E731
    return round((parse(end) - parse(start)).total_seconds(), 1)


def collect(ws: Workspace, vehicle_ids: list[str]) -> list[dict]:
    rows = []
    for vehicle_id in vehicle_ids:
        for attempt in ws.attempts(vehicle_id):
            state = read_state(attempt)
            norm_path = attempt / "normalize.json"
            norm = json.loads(norm_path.read_text(encoding="utf-8")) if norm_path.is_file() else {}
            rows.append({
                "vehicle": vehicle_id,
                "attempt": attempt.name,
                "status": state.get("status"),
                "task_id": state.get("task_id"),
                "api_seconds": _seconds(state.get("api_created_at"), state.get("api_completed_at")),
                "local_wall_seconds": state.get("local_wall_seconds"),
                "credits": state.get("credits_consumed"),
                "triangles": norm.get("triangles"),
                "size_m": norm.get("result_m"),
                "method": norm.get("method"),
                "warnings": norm.get("warnings", []),
                "error": state.get("error_message") or state.get("error") or state.get("last_error"),
            })
    return rows


def markdown(rows: list[dict]) -> str:
    lines = [
        "# Tripo fleet runs",
        "",
        f"Generated {datetime.now():%Y-%m-%d %H:%M}. API time = Tripo's created_at → completed_at. "
        "Wall time = local, including uploads and polling.",
        "",
        "| Vehicle | Attempt | Status | API s | Wall s | Credits | Triangles | L × W × H m | Notes |",
        "|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    total_credits = 0.0
    for r in rows:
        size = r["size_m"]
        dims = f"{size['length']:.2f} × {size['width']:.2f} × {size['height']:.2f}" if size else ""
        notes = "; ".join(r["warnings"] + ([r["error"]] if r["error"] else []))
        lines.append(f"| {r['vehicle']} | {r['attempt']} | {r['status']} | {r['api_seconds'] or ''} | "
                     f"{r['local_wall_seconds'] or ''} | {r['credits'] or ''} | {r['triangles'] or ''} | "
                     f"{dims} | {notes} |")
        total_credits += float(r["credits"] or 0)
    lines += ["", f"Total credits consumed: {total_credits:.2f} across {len(rows)} attempts.", ""]
    return "\n".join(lines)
