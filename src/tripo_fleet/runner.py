"""Generation runs: upload → multiview task → poll → download → normalise, with an append-only ledger.

Every attempt keeps its own folder, including failures, so the video can show real numbers:
output/<vehicle>/attempt-NN/{state.json, request.json, raw.glb, preview.*, <vehicle>_tripo.glb,
normalize.json, textures/}. output/ledger.jsonl records every step with a local timestamp.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import gltf_norm
from .api import TripoClient, TripoError, download
from .fleet import Vehicle

_print_lock = threading.Lock()
_ledger_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def say(vehicle_id: str, message: str) -> None:
    with _print_lock:
        print(f"{datetime.now():%H:%M:%S}  {vehicle_id:<16} {message}", flush=True)


class Workspace:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.ledger_path = root / "ledger.jsonl"

    def log(self, vehicle_id: str, event: str, **data) -> None:
        line = json.dumps({"time": now(), "vehicle": vehicle_id, "event": event, **data})
        with _ledger_lock, self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def attempts(self, vehicle_id: str) -> list[Path]:
        folder = self.root / vehicle_id
        return sorted(folder.glob("attempt-*")) if folder.is_dir() else []

    def new_attempt(self, vehicle_id: str) -> Path:
        number = len(self.attempts(vehicle_id)) + 1
        path = self.root / vehicle_id / f"attempt-{number:02d}"
        path.mkdir(parents=True)
        return path


def read_state(attempt: Path) -> dict:
    path = attempt / "state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_state(attempt: Path, state: dict) -> None:
    (attempt / "state.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def succeeded(ws: Workspace, vehicle_id: str) -> Path | None:
    for attempt in reversed(ws.attempts(vehicle_id)):
        if read_state(attempt).get("status") == "success" and (attempt / f"{vehicle_id}_tripo.glb").is_file():
            return attempt
    return None


def _model_url(output: dict) -> str | None:
    return output.get("model_url") or output.get("pbr_model") or output.get("model")


def finish(client: TripoClient, ws: Workspace, vehicle: Vehicle, attempt: Path, state: dict,
           timeout: float) -> dict:
    """Poll an existing task to the end, download immediately, then normalise."""
    task_id = state["task_id"]

    def progress(task: dict) -> None:
        say(vehicle.id, f"{task.get('status')} {task.get('progress', 0)}%")

    try:
        task = client.wait(task_id, timeout=timeout, on_progress=progress)
    except TimeoutError as exc:
        state["status"] = "timeout"
        write_state(attempt, state)
        ws.log(vehicle.id, "timeout", task_id=task_id)
        say(vehicle.id, f"{exc}. Resume later with `tripo-fleet resume`.")
        return state

    state.update(status=task["status"], api_created_at=task.get("created_at"),
                 api_completed_at=task.get("completed_at"), credits_consumed=task.get("credits_consumed"),
                 finished_local=now())
    write_state(attempt, state)  # a crash during download leaves "success" without raw.glb → resume
    if task["status"] != "success":
        state.update(error_code=task.get("error_code"), error_message=task.get("error_message"))
        write_state(attempt, state)
        ws.log(vehicle.id, "task_" + task["status"], task_id=task_id, error_code=task.get("error_code"))
        say(vehicle.id, f"task ended {task['status']}: {task.get('error_message', '')}")
        return state

    output = task.get("output") or {}
    url = _model_url(output)
    if not url:
        state["status"] = "no_model_url"
        write_state(attempt, state)
        ws.log(vehicle.id, "no_model_url", task_id=task_id, output_keys=sorted(output))
        return state
    (attempt / "task-result.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix not in (".glb", ".fbx"):
        raise ValueError(f"unsupported model format: {suffix}")
    raw = attempt / f"raw{suffix}"
    size = raw.stat().st_size if raw.is_file() else download(url, raw)
    state["raw_file"] = raw.name
    state["raw_bytes"] = size
    if output.get("rendered_image_url"):
        ext = Path(output["rendered_image_url"].split("?")[0]).suffix or ".png"
        download(output["rendered_image_url"], attempt / f"preview{ext}")
    write_state(attempt, state)
    ws.log(vehicle.id, "downloaded", task_id=task_id, bytes=size,
           credits=task.get("credits_consumed"))
    say(vehicle.id, f"downloaded {size / 1e6:.1f} MB, {task.get('credits_consumed')} credits")
    normalize_attempt(ws, vehicle, attempt)
    state["normalization_status"] = "complete"
    write_state(attempt, state)
    return state


def normalize_attempt(ws: Workspace, vehicle: Vehicle, attempt: Path) -> dict:
    source = attempt / "raw.glb"
    native = None
    if (attempt / "raw.fbx").is_file():
        source = attempt / "converted.glb"
        if not source.is_file() or not (attempt / "fbx-inspection.json").is_file():
            blender = os.environ.get("BLENDER_EXE") or shutil.which("blender") or r"E:\Blender\blender.exe"
            conversion = subprocess.run(
                [blender, "--background", "--factory-startup", "--python",
                 str(Path(__file__).with_name("blender_fbx.py")), "--", str(attempt.resolve())],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
            )
            (attempt / "conversion.log").write_text(conversion.stdout + conversion.stderr, encoding="utf-8")
            if conversion.returncode or not source.is_file():
                raise RuntimeError("local FBX conversion failed; see conversion.log")
        native = json.loads((attempt / "fbx-inspection.json").read_text(encoding="utf-8"))
    result = gltf_norm.normalize(
        source, attempt / f"{vehicle.id}_tripo.glb",
        length_m=vehicle.length_m, source_forward=vehicle.source_forward,
        textures_dir=attempt / "textures", expected_width_m=vehicle.width_m,
        expected_height_m=vehicle.height_m,
        face_limit=None if vehicle.generation.get("quad") else vehicle.generation.get("face_limit"),
    )
    result["generation_face_limit"] = vehicle.generation.get("face_limit")
    if native:
        result["native_polygon_counts"] = native["polygon_vertex_counts"]
        result["native_total_polygons"] = native["total_polygons"]
        if native["total_polygons"] > vehicle.generation.get("face_limit", float("inf")):
            result["warnings"].append(f"native polygons {native['total_polygons']} exceed requested budget {vehicle.generation['face_limit']}")
    (attempt / "normalize.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    ws.log(vehicle.id, "normalized", attempt=attempt.name, method=result["method"],
           triangles=result["triangles"], native_polygons=result.get("native_total_polygons"), warnings=result["warnings"])
    dims = result["result_m"]
    say(vehicle.id, f"normalized ({result['method']}): {dims['length']:.2f} x {dims['width']:.2f} x "
                    f"{dims['height']:.2f} m, {result['triangles']} tris")
    for warning in result["warnings"]:
        say(vehicle.id, f"WARNING {warning}")
    return result


def generate(client: TripoClient, ws: Workspace, vehicle: Vehicle, timeout: float) -> dict:
    attempt = ws.new_attempt(vehicle.id)
    state = {"vehicle": vehicle.id, "attempt": attempt.name, "status": "uploading",
             "started_local": now(), "views": {k: str(v) for k, v in vehicle.views.items()}}
    write_state(attempt, state)
    started = time.monotonic()
    try:
        tokens = {}
        for view, path in vehicle.views.items():
            tokens[view] = client.upload(path)
            ws.log(vehicle.id, "uploaded", view=view, file=path.name)
            say(vehicle.id, f"uploaded {view}")
        payload = vehicle.payload(tokens)
        (attempt / "request.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        state["task_id"] = client.create_multiview(payload)
        state.update(status="submitted", submitted_local=now())
        write_state(attempt, state)
        ws.log(vehicle.id, "submitted", task_id=state["task_id"], model=payload.get("model"),
               face_limit=payload.get("face_limit"))
        say(vehicle.id, f"submitted {state['task_id']}")
        state = finish(client, ws, vehicle, attempt, state, timeout)
    except TripoError as exc:
        state.update(status="api_error", error=str(exc))
        write_state(attempt, state)
        ws.log(vehicle.id, "api_error", code=exc.code, http=exc.http_status, request_id=exc.request_id)
        say(vehicle.id, f"API error: {exc}")
    except Exception as exc:  # download or normalise failure: keep the task, resume can finish it
        state = {**read_state(attempt), "last_error": f"{type(exc).__name__}: {exc}"}
        ws.log(vehicle.id, "error", error=state["last_error"], task_id=state.get("task_id"))
        say(vehicle.id, f"ERROR {state['last_error']}")
    state["local_wall_seconds"] = round(time.monotonic() - started, 1)
    write_state(attempt, state)
    return state


def resume(client: TripoClient, ws: Workspace, vehicle: Vehicle, timeout: float) -> list[dict]:
    results = []
    for attempt in ws.attempts(vehicle.id):
        state = read_state(attempt)
        if not state.get("task_id"):
            continue
        open_task = state.get("status") in ("submitted", "timeout", "queued", "running")
        missing_download = state.get("status") == "success" and not (attempt / f"{vehicle.id}_tripo.glb").is_file()
        if open_task or missing_download:
            say(vehicle.id, f"resuming {attempt.name} ({state['task_id']})")
            results.append(finish(client, ws, vehicle, attempt, state, timeout))
    return results
