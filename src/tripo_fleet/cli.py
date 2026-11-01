"""tripo-fleet: init, plan, balance, run, resume, normalize, report."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, fleet, report, runner
from .api import TripoClient, TripoError

ROOT = config.PROJECT_ROOT
# One documented P-series example consumed 100 credits. Real costs come from credits_consumed.
CREDITS_ESTIMATE_PER_JOB = 110.0
P_SERIES_CONCURRENCY = 5


def _client() -> TripoClient:
    return TripoClient(config.api_key(), config.api_base())


def _vehicles(args) -> list[fleet.Vehicle]:
    vehicles = fleet.load(args.fleet)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {v.id for v in vehicles}
        if unknown:
            sys.exit(f"unknown vehicle id(s): {', '.join(sorted(unknown))}")
        vehicles = [v for v in vehicles if v.id in wanted]
    return vehicles


def cmd_init(args) -> int:
    if args.fleet.exists() and not args.overwrite:
        sys.exit(f"{args.fleet} exists; pass --overwrite to regenerate it from the recipe")
    data = fleet.init_from_recipe(args.recipe, args.fleet, ROOT / "concepts")
    print(f"wrote {args.fleet} with {len(data['vehicles'])} vehicles; concept folders under concepts/")
    return 0


def cmd_plan(args) -> int:
    vehicles = _vehicles(args)
    ws = runner.Workspace(args.output)
    ready = 0
    for v in vehicles:
        done = runner.succeeded(ws, v.id)
        views = ", ".join(v.views) or "none"
        status = "done (" + done.name + ")" if done else ("BLOCKED" if v.problems else "ready")
        print(f"{v.id:<16} {status:<18} views: {views:<26} face_limit {v.generation.get('face_limit')}"
              f"  model {v.generation.get('model')}")
        for problem in v.problems:
            print(f"{'':<16} - {problem}")
        if not v.problems and not done:
            ready += 1
    estimate = ready * CREDITS_ESTIMATE_PER_JOB
    print(f"\n{ready} job(s) ready, roughly {estimate:.0f} credits "
          f"(estimate: {CREDITS_ESTIMATE_PER_JOB:.0f} per job from Tripo's documented example).")
    if args.balance:
        return cmd_balance(args, estimate)
    return 0


def cmd_balance(args, needed: float | None = None) -> int:
    try:
        data = _client().balance()
    except TripoError as exc:
        print(f"balance check failed: {exc}")
        if exc.http_status in (401, 403):
            print("Check the key and that API registration (the API wallet) is complete on the "
                  "developer platform with the same email as the membership.")
        return 1
    print(f"balance {data.get('balance')} credits, frozen {data.get('frozen')}")
    if needed is not None and float(data.get("balance") or 0) < needed:
        print(f"WARNING: estimated {needed:.0f} credits needed")
    return 0


def cmd_run(args) -> int:
    vehicles = _vehicles(args)
    ws = runner.Workspace(args.output)
    todo = []
    for v in vehicles:
        if v.problems:
            print(f"skip {v.id}: " + "; ".join(v.problems))
        elif runner.succeeded(ws, v.id) and not args.force:
            print(f"skip {v.id}: already generated (use --force for a new attempt)")
        else:
            todo.append(v)
    if not todo:
        print("nothing to run")
        return 0
    estimate = len(todo) * CREDITS_ESTIMATE_PER_JOB
    print(f"{len(todo)} job(s): {', '.join(v.id for v in todo)}. About {estimate:.0f} credits (estimate).")
    if not args.go:
        print("Dry run. Add --go to upload and spend credits.")
        return 0
    client = _client()
    workers = max(1, min(args.parallel, P_SERIES_CONCURRENCY))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda v: runner.generate(client, ws, v, args.timeout), todo))
    ok = sum(1 for r in results if r.get("status") == "success" and r.get("normalization_status") == "complete")
    print(f"\n{ok}/{len(results)} succeeded. `tripo-fleet report` summarises all attempts.")
    return 0 if ok == len(results) else 1


def cmd_resume(args) -> int:
    client = _client()
    ws = runner.Workspace(args.output)
    for v in _vehicles(args):
        runner.resume(client, ws, v, args.timeout)
    return 0


def cmd_normalize(args) -> int:
    ws = runner.Workspace(args.output)
    for v in _vehicles(args):
        for attempt in ws.attempts(v.id):
            if (attempt / "raw.glb").is_file() or (attempt / "raw.fbx").is_file():
                runner.normalize_attempt(ws, v, attempt)
    return 0


def cmd_report(args) -> int:
    ids = [v.id for v in _vehicles(args)]
    rows = report.collect(runner.Workspace(args.output), ids)
    text = report.markdown(rows)
    (args.output / "report.md").write_text(text, encoding="utf-8")
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Reports contain arrows and dimensions; redirected Windows stdout may default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="tripo-fleet", description=__doc__)
    parser.add_argument("--fleet", type=Path, default=ROOT / "fleet.json")
    parser.add_argument("--output", type=Path, default=ROOT / "output")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create fleet.json and concept folders from the GeometryForge recipe")
    p.add_argument("--recipe", type=Path, required=True,
                   help="path to your GeometryForge fleet recipe (not needed for the included fleet)")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_init)

    for name, func, help_text in (
        ("plan", cmd_plan, "check views and settings; spends nothing"),
        ("run", cmd_run, "upload, generate, download, normalise (needs --go)"),
        ("resume", cmd_resume, "finish tasks that timed out or failed to download"),
        ("normalize", cmd_normalize, "re-run normalisation on downloaded raw.glb files"),
        ("report", cmd_report, "summarise all attempts"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--only", nargs="+", metavar="ID")
        p.set_defaults(func=func)
        if name == "plan":
            p.add_argument("--balance", action="store_true", help="also query the credit balance")
        if name in ("run", "resume"):
            p.add_argument("--timeout", type=float, default=1200.0, help="seconds per task")
        if name == "run":
            p.add_argument("--go", action="store_true", help="actually spend credits")
            p.add_argument("--force", action="store_true", help="new attempt even if one succeeded")
            p.add_argument("--parallel", type=int, default=3, help="concurrent jobs (P series max 5)")
        if name == "report":
            p.add_argument("--json", type=Path, help="also write rows as JSON, e.g. into the episode research")

    p = sub.add_parser("balance", help="query the credit balance (also proves the key and wallet work)")
    p.set_defaults(func=lambda a: cmd_balance(a))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
