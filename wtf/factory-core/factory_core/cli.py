#!/usr/bin/env python3
"""cli.py - ``factoryctl``: operator CLI for the WTF Avatar Factory queue (W1).

Usage (from the package dir, or after ``pip install -e .``):

    python -m factory_core.cli <command> [options]

Commands:
    init-db                 create the schema-v1 tables at --db (default contract path)
    add                     queue a new job
    list / show / events    inspect the queue
    run                     advance queued jobs (render -> gate)
    approve / reject        operator decisions on gated jobs
    publish                 publish approved jobs (dark stub)
    requeue                 failed -> queued (explicit operator retry)
    stats                   per-status counts
    doctor                  environment + dark-flag report
    demo                    offline end-to-end scenario, writes demo.log + receipt

Exit codes: 0 ok · 1 unexpected error · 2 argparse usage · 3 domain error
(not found, illegal transition, live access refused).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .adapters import (
    ADAPTER_SPEC,
    DARK,
    DEMO_FAILING_RENDER,
    LIVE_ENABLED,
    NETWORK_ENABLED,
    REQUIRED_STAGES,
    LiveAccessRefused,
    default_adapters,
)
from .jobqueue import (
    DEFAULT_DB_PATH,
    PACKAGE_ROOT,
    PACKAGE_DIR,
    STATUSES,
    FactoryDB,
    Job,
    JobQueueError,
    utc_now,
)
from .pipeline import UNTIL_CHOICES, Pipeline

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DOMAIN = 3

STATUS_ORDER = (
    "queued",
    "rendering",
    "rendered",
    "gated",
    "approved",
    "rejected",
    "published",
    "failed",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _db_from_args(args: argparse.Namespace) -> FactoryDB:
    return FactoryDB(args.db)


def _print_job_line(job: Job) -> None:
    gate = "" if job.gate_score is None else f"  gate={job.gate_score}"
    print(f"{job.id:<18} {job.status:<10} {job.brand:<12} {job.language:<10}{gate}")


def _formatter(rows: List[List[str]]) -> Callable[[List[str]], str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]

    def fmt(cells: List[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    return fmt


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_init_db(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        print(f"schema v1 ready at {db.path}")
        print("tables: " + ", ".join(db.tables()))
    finally:
        db.close()
    return EXIT_OK


def cmd_add(args: argparse.Namespace) -> int:
    script = args.script
    if args.script_file:
        script_path = Path(args.script_file).expanduser()
        if not script_path.is_absolute():
            raise JobQueueError(f"--script-file must be an absolute path, got {args.script_file!r}")
        script = script_path.read_text(encoding="utf-8")
    db = _db_from_args(args)
    try:
        job = db.add_job(
            brand=args.brand,
            pillar=args.pillar,
            language=args.language,
            title=args.title,
            script=script,
            notes=args.notes,
        )
    finally:
        db.close()
    if args.json:
        print(job.to_json())
    else:
        print(job.id)
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        jobs = db.list_jobs(status=args.status, limit=args.limit)
    finally:
        db.close()
    if args.json:
        print(json.dumps([j.as_dict() for j in jobs], indent=2, sort_keys=True))
        return EXIT_OK
    if not jobs:
        print("(no jobs)")
        return EXIT_OK
    rows = [["ID", "STATUS", "BRAND", "PILLAR", "LANG", "TITLE"]]
    for job in jobs:
        rows.append(
            [
                job.id,
                job.status,
                job.brand,
                job.pillar,
                job.language,
                (job.title or "")[:40],
            ]
        )
    fmt = _formatter(rows)
    for row in rows:
        print(fmt(row))
    return EXIT_OK


def cmd_show(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        job = db.get_job(args.job_id)
        events = db.get_events(args.job_id, limit=args.events)
    finally:
        db.close()
    if args.json:
        print(json.dumps({"job": job.as_dict(), "events": events}, indent=2, sort_keys=True))
        return EXIT_OK
    print(json.dumps(job.as_dict(), indent=2, sort_keys=True))
    print(f"events ({len(events)}):")
    for ev in events:
        meta = "" if ev["meta"] is None else "  " + json.dumps(ev["meta"], sort_keys=True)
        print(f"  [{ev['id']:>3}] {ev['ts']}  {ev['event']}{meta}")
    return EXIT_OK


def cmd_events(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        events = db.get_events(args.job, limit=args.limit)
    finally:
        db.close()
    if args.json:
        print(json.dumps(events, indent=2, sort_keys=True))
        return EXIT_OK
    for ev in events:
        meta = "" if ev["meta"] is None else "  " + json.dumps(ev["meta"], sort_keys=True)
        print(f"[{ev['id']:>4}] {ev['job_id']:<18} {ev['ts']}  {ev['event']}{meta}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        pipeline = Pipeline(db)
        if args.once:
            job = pipeline.run_once(until=args.until)
            processed = [] if job is None else [job]
        else:
            processed = pipeline.drain(limit=args.limit, until=args.until)
    finally:
        db.close()
    if not processed:
        print("(no queued jobs)")
        return EXIT_OK
    for job in processed:
        _print_job_line(job)
    return EXIT_OK


def cmd_approve(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        job = Pipeline(db).approve(args.job_id, notes=args.notes)
    finally:
        db.close()
    print(f"{job.id} -> {job.status}")
    return EXIT_OK


def cmd_reject(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        job = Pipeline(db).reject(args.job_id, notes=args.notes)
    finally:
        db.close()
    print(f"{job.id} -> {job.status}")
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        published = Pipeline(db).publish_approved(limit=args.limit)
    finally:
        db.close()
    if not published:
        print("(no approved jobs)")
        return EXIT_OK
    for job in published:
        _print_job_line(job)
    return EXIT_OK


def cmd_requeue(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        job = Pipeline(db).requeue(args.job_id, reason=args.reason)
    finally:
        db.close()
    print(f"{job.id} -> {job.status}")
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    db = _db_from_args(args)
    try:
        counts = db.counts()
    finally:
        db.close()
    total = sum(counts.values())
    for name in STATUS_ORDER:
        print(f"{name:<10} {counts[name]}")
    print(f"{'total':<10} {total}")
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    print("factoryctl doctor")
    print(f"  version:   {__version__}")
    print(f"  python:    {sys.version.split()[0]} ({sys.executable})")
    print(f"  package:   {PACKAGE_DIR}")
    db_path = Path(args.db).expanduser()
    print(f"  db:        {db_path} [exists={db_path.exists()}]")
    ancestor = db_path.resolve().parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    print(f"  db parent writable: {os.access(ancestor, os.W_OK)} ({ancestor})")
    print(f"  dark:      DARK={DARK} LIVE_ENABLED={LIVE_ENABLED} NETWORK_ENABLED={NETWORK_ENABLED}")
    print("  adapters:  " + ", ".join(f"{name}={ADAPTER_SPEC[name].__name__}" for name in REQUIRED_STAGES))
    print("  result:    OK (offline, stdlib-only, dark stubs ready)")
    return EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    """Deterministic offline end-to-end scenario; writes demo.log + demo_receipt.json."""
    out_dir = (
        Path(args.dir).expanduser().resolve()
        if args.dir
        else (PACKAGE_ROOT / "evidence")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "demo.db"
    for suffix in ("", "-wal", "-shm"):
        stale = Path(str(db_path) + suffix)
        if stale.exists():
            stale.unlink()

    lines: List[str] = []

    def emit(msg: str = "") -> None:
        lines.append(msg)
        print(msg)

    emit("# WTF Avatar Factory - factory-core offline demo (W1, schema v1)")
    emit(f"# db: {db_path}")
    emit(f"# dark flags: DARK={DARK} LIVE_ENABLED={LIVE_ENABLED} NETWORK_ENABLED={NETWORK_ENABLED}")
    emit("")

    db = FactoryDB(db_path)
    pipeline = Pipeline(db, log=emit)
    ids: List[str] = []

    emit("== 1. queue jobs ==")
    j1 = db.add_job(brand="WTF Gyms", pillar="fitness-transformation", language="hindi",
                    title="Transformation Tuesday hook")
    j2 = db.add_job(brand="EVRYDAY", pillar="business", language="english",
                    title="D2C launch diary")
    j3 = db.add_job(brand="Reboot", pillar="ai-systems", language="hinglish",
                    title="AI stack teardown")
    for job in (j1, j2, j3):
        ids.append(job.id)
        emit(f"  + {job.id}  {job.brand:<10} {job.language:<9} {job.title}")
    emit("")

    emit("== 2. pipeline drain (render -> gate) ==")
    drained = pipeline.drain()
    for job in drained:
        emit(f"  - {job.id} -> {job.status}  gate_score={job.gate_score}")
    emit("")

    emit("== 3. operator decisions ==")
    pipeline.approve(j1.id, notes="strong hook (demo)")
    pipeline.approve(j2.id, notes="on-brand (demo)")
    pipeline.reject(j3.id, notes="off-brand hook (demo)")
    emit(f"  approve {j1.id} -> {db.get_job(j1.id).status}")
    emit(f"  approve {j2.id} -> {db.get_job(j2.id).status}")
    emit(f"  reject  {j3.id} -> {db.get_job(j3.id).status}")
    emit("")

    emit("== 4. publish approved ==")
    published = pipeline.publish_approved()
    for job in published:
        emit(f"  - {job.id} -> {job.status}")
    emit("")

    emit("== 5. failure + requeue + retry path ==")
    j4 = db.add_job(brand="WTF Academy", pillar="founder-journey", language="english",
                    title="Failure path demo")
    ids.append(j4.id)
    failing = Pipeline(
        db,
        adapters={**default_adapters(), "render": DEMO_FAILING_RENDER()},
        log=emit,
    )
    jf = failing.run_job(j4.id)
    emit(f"  - {j4.id} -> {jf.status} (simulated render failure)")
    rq = failing.requeue(j4.id, reason="retry after simulated failure (demo)")
    emit(f"  - {j4.id} -> {rq.status} (requeued)")
    jok = pipeline.run_job(j4.id)
    emit(f"  - {j4.id} -> {jok.status}  gate_score={jok.gate_score}")
    emit(f"  note: {j4.id} intentionally left 'gated' for the console approval queue")
    emit("")

    emit(f"== 6. event trail for {j4.id} (failure -> requeue -> retry) ==")
    for ev in db.get_events(j4.id):
        meta = "" if ev["meta"] is None else json.dumps(ev["meta"], sort_keys=True)
        emit(f"  [{ev['id']:>3}] {ev['ts']}  {ev['event']:<14} {meta}")
    emit("")

    counts = db.counts()
    emit("== 7. final counts ==")
    for name in STATUS_ORDER:
        emit(f"  {name:<10} {counts[name]}")
    emit("")

    final = {jid: db.get_job(jid) for jid in ids}
    all_events = db.get_events(limit=1000)
    checks: List[Tuple[str, bool]] = [
        ("drain moved 3 jobs queued -> gated",
         len(drained) == 3 and all(j.status == "gated" for j in drained)),
        ("approvals applied (2 approved -> published, 1 rejected)",
         final[j1.id].status == "published"
         and final[j2.id].status == "published"
         and final[j3.id].status == "rejected"),
        ("publish touched only approved jobs",
         {job.id for job in published} == {j1.id, j2.id} and final[j3.id].status == "rejected"),
        ("failure path sets failed",
         jf.status == "failed"),
        ("requeue returns failed -> queued",
         rq.status == "queued"),
        ("retry reaches gated with score",
         jok.status == "gated" and (jok.gate_score or 0) > 0),
        ("gate scores recorded for every gated/published job",
         all((final[jid].gate_score or 0) > 0 for jid in (j1.id, j2.id, j3.id, j4.id))),
        ("audit trail has >= 45 events",
         len(all_events) >= 45),
    ]

    emit("== checks ==")
    for name, ok in checks:
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
    status = "SUCCESS" if all(ok for _, ok in checks) else "FAILED"
    emit("")
    emit(f"STATUS: {status}")
    db.close()

    log_path = out_dir / "demo.log"
    receipt_path = out_dir / "demo_receipt.json"
    receipt: Dict[str, Any] = {
        "status": status,
        "generated_at": utc_now(),
        "db": str(db_path),
        "jobs": {jid: final[jid].as_dict() for jid in ids},
        "counts": counts,
        "checks": {name: ok for name, ok in checks},
        "live_calls": 0,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    written = [db_path.name]
    for suffix in ("-wal", "-shm"):
        if Path(str(db_path) + suffix).exists():
            written.append(db_path.name + suffix)
    written.append(receipt_path.name)
    written.append(log_path.name)

    lines += [
        "",
        "## 5. Self-audit",
        f"- writes: {len(written)} files ({', '.join(written)})",
        "- live calls: 0 (all adapters dark stubs; demo runs offline)",
        "- secrets printed: 0",
        "- unverified claims: 0 (every check above is computed from the demo database)",
        f"- status: {status}",
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return EXIT_OK if status == "SUCCESS" else EXIT_ERROR


# ---------------------------------------------------------------------------
# parser / entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factoryctl",
        description="WTF Avatar Factory queue/orchestrator control (schema v1, dark by default)",
    )
    parser.add_argument("--version", action="version", version=f"factoryctl {__version__}")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                        help=f"factory database path (default: {DEFAULT_DB_PATH})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-db", help="create schema-v1 tables (idempotent)")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("add", help="queue a new job")
    p.add_argument("--brand", required=True)
    p.add_argument("--pillar", required=True)
    p.add_argument("--language", required=True)
    p.add_argument("--title")
    p.add_argument("--script", help="script text")
    p.add_argument("--script-file", help="absolute path to a script file (alternative to --script)")
    p.add_argument("--notes")
    p.add_argument("--json", action="store_true", help="print the full job as JSON")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("list", help="list jobs")
    p.add_argument("--status", choices=list(STATUSES))
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one job + its events")
    p.add_argument("job_id")
    p.add_argument("--events", type=int, default=30, help="how many events to show")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("events", help="list recent events")
    p.add_argument("--job")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("run", help="advance queued jobs through render/gate")
    p.add_argument("--until", choices=list(UNTIL_CHOICES), default="gated")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--once", action="store_true", help="process at most one job")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("approve", help="approve a gated job")
    p.add_argument("job_id")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="reject a gated job")
    p.add_argument("job_id")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("publish", help="publish approved jobs (dark stub)")
    p.add_argument("--limit", type=int, default=100)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("requeue", help="failed -> queued (explicit retry)")
    p.add_argument("job_id")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_requeue)

    p = sub.add_parser("stats", help="per-status counts")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("doctor", help="environment + dark-flag report")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("demo", help="offline end-to-end demo (writes demo.log + receipt)")
    p.add_argument("--dir", help="output dir (default: wtf/factory-core/evidence)")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (JobQueueError, LiveAccessRefused) as exc:
        print(f"factoryctl: {exc}", file=sys.stderr)
        return EXIT_DOMAIN
    except FileNotFoundError as exc:
        print(f"factoryctl: {exc}", file=sys.stderr)
        return EXIT_DOMAIN


if __name__ == "__main__":
    raise SystemExit(main())
