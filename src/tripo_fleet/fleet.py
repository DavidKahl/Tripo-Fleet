"""fleet.json: which vehicles to generate, their reference views and generation settings."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

VIEWS = ("front", "left", "back", "right")  # the only views multiview accepts
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")  # what POST /files accepts for images
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MIN_IMAGE_SIDE = 256
FORWARD_AXES = ("+x", "-x", "+z", "-z")

# Only documented request fields may pass through to the API
GENERATION_FIELDS = {
    "model", "texture_alignment", "orientation", "model_seed", "face_limit", "texture", "pbr",
    "texture_seed", "texture_quality", "texture_version", "delight", "auto_size", "export_uv",
    "export_orientation", "quad",
}

DEFAULT_GENERATION = {
    "model": "P1-20260311",
    "texture": True,
    "pbr": True,
    "model_seed": 0,
    "texture_seed": 0,
}


@dataclass
class Vehicle:
    id: str
    family: str
    length_m: float
    width_m: float
    height_m: float
    views: dict[str, Path]
    generation: dict
    source_forward: str = "+x"
    notes: str = ""
    problems: list[str] = field(default_factory=list)

    def payload(self, tokens: dict[str, str]) -> dict:
        body = {"inputs": [{view: tokens[view]} for view in VIEWS if view in tokens]}
        body.update(self.generation)
        return body


def _resolve_view(base: Path, value: str | None, vehicle_dir: Path, view: str) -> Path | None:
    if value:
        path = Path(value)
        return path if path.is_absolute() else (base / path)
    for suffix in IMAGE_SUFFIXES:
        candidate = vehicle_dir / f"{view}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def check_image(path: Path) -> list[str]:
    problems = []
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        problems.append(f"{path.name}: upload accepts PNG/JPEG only")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        problems.append(f"{path.name}: larger than 20 MB")
    try:
        with Image.open(path) as image:
            if min(image.size) < MIN_IMAGE_SIDE:
                problems.append(f"{path.name}: {image.size[0]}x{image.size[1]} is below 256 px")
    except OSError as exc:
        problems.append(f"{path.name}: unreadable image ({exc})")
    return problems


def load(fleet_path: Path) -> list[Vehicle]:
    data = json.loads(fleet_path.read_text(encoding="utf-8"))
    base = fleet_path.parent
    concepts = base / data.get("concepts_dir", "concepts")
    defaults = {**DEFAULT_GENERATION, **data.get("generation", {})}
    vehicles = []
    for entry in data["vehicles"]:
        generation = {**defaults, **entry.get("generation", {})}
        unknown = set(generation) - GENERATION_FIELDS
        vehicle_dir = concepts / entry["id"]
        views = {}
        for view in VIEWS:
            path = _resolve_view(base, entry.get("views", {}).get(view), vehicle_dir, view)
            if path is not None and path.is_file():
                views[view] = path
        vehicle = Vehicle(
            id=entry["id"], family=entry["family"],
            length_m=float(entry["length_m"]), width_m=float(entry["width_m"]),
            height_m=float(entry["height_m"]), views=views,
            generation={k: v for k, v in generation.items() if k in GENERATION_FIELDS},
            source_forward=entry.get("source_forward", data.get("source_forward", "+x")),
            notes=entry.get("notes", ""),
        )
        if unknown:
            vehicle.problems.append(f"unknown generation fields ignored: {sorted(unknown)}")
        if "front" not in views:
            vehicle.problems.append(f"front view missing (expected in {vehicle_dir})")
        if len(views) < 2:
            vehicle.problems.append(f"only {len(views)} view(s); multiview needs at least 2")
        if vehicle.source_forward not in FORWARD_AXES:
            vehicle.problems.append(f"source_forward must be one of {FORWARD_AXES}")
        for path in views.values():
            vehicle.problems.extend(check_image(path))
        vehicles.append(vehicle)
    return vehicles


def init_from_recipe(recipe_path: Path, fleet_path: Path, concepts_dir: Path) -> dict:
    """Write fleet.json from the GeometryForge fleet recipe, so both fleets share ids and sizes."""
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    budgets = {name: family["budgets"][0] for name, family in recipe["families"].items()}
    fleet = {
        "_about": "Tripo fleet. ids, sizes and face budgets mirror the GeometryForge low_poly_fleet recipe "
                  "so both fleets plug into the same Unreal catalog records.",
        "recipe": str(recipe_path),
        "concepts_dir": concepts_dir.name,
        "source_forward": "+x",
        "generation": dict(DEFAULT_GENERATION),
        "vehicles": [
            {
                "id": v["id"], "family": v["family"],
                "length_m": v["length"], "width_m": v["width"], "height_m": v["height"],
                "generation": {"face_limit": budgets[v["family"]]},
            }
            for v in recipe["vehicles"]
        ],
    }
    fleet_path.write_text(json.dumps(fleet, indent=2) + "\n", encoding="utf-8")
    for v in recipe["vehicles"]:
        (concepts_dir / v["id"]).mkdir(parents=True, exist_ok=True)
    return fleet
