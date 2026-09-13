#!/usr/bin/env python3
"""Live kanban-swarm dashboard -> <project>/army_dashboard.html (self-refreshing).

Distilled from the working generator. Stdlib only. Headless-safe (no browser needed).

Usage:
    python3 army_dashboard.py --board wtf-thesis --project ~/wtf-thesis \
        [--pattern '*.xlsx' --pattern 'facts_from_email.json'] [--tail 1]

Loop it (e.g. `while true; do ...; sleep 10; done` in a silent background process);
the emitted HTML meta-refreshes every 10s so a chat preview pane stays live.
Surface with ::preview{file="<project>/army_dashboard.html"} and ALSO quote the raw
proof lines once (live ps rows + board status counts).
"""
import argparse, glob, html, json, os, pathlib, re, subprocess, time

GLYPH = {"running": "\U0001F528", "done": "\u2705", "ready": "\U000023F3",
         "todo": "\u25FB", "blocked": "\U0001F512", "failed": "\u274C"}

def sh(cmd):
    # commands are static constants built from CLI args; no untrusted input.
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""

def board_tasks(board):
    out = sh(f"hermes kanban --board {board} list --json 2>/dev/null")
    if not out.strip():
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    return tasks if isinstance(tasks, list) else []

def live_workers():
    # task id is the last token of each `... work kanban task <id>` process line
    rows = {}
    for line in sh("ps aux | grep '[w]ork kanban task'").splitlines():
        m = re.search(r"work kanban task (\S+)", line)
        if not m:
            continue
        parts = line.split()
        cpu = next((p for p in parts if re.fullmatch(r"\d+\.\d+", p)), "")
        rows.setdefault(m.group(1), []).append(cpu)
    return rows

def tail_log(board, task_id, n=1):
    p = pathlib.Path.home() / ".hermes" / "kanban" / "boards" / board / "logs" / f"{task_id}.log"
    if not p.exists():
        return ""
    try:
        lines = [l for l in p.read_text(errors="replace").splitlines() if l.strip()]
        return " | ".join(l[:160] for l in lines[-n:])
    except OSError:
        return ""

def count_artifacts(root, patterns):
    counts = {}
    for pat in patterns:
        hits = [f for f in glob.glob(str(pathlib.Path(root) / "**" / pat), recursive=True)]
        counts[pat] = len(hits)
    return counts

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--pattern", action="append",
                    default=["*.xlsx", "*.pptx", "facts_from_email.json", "model.json"])
    ap.add_argument("--title", default=None)
    args = ap.parse_args()
    root = pathlib.Path(os.path.expanduser(args.project))

    tasks = board_tasks(args.board)
    workers = live_workers()
    counts = count_artifacts(root, args.pattern)

    def st(t):
        return (t.get("status") or t.get("state") or "?") if isinstance(t, dict) else "?"

    def tid(t):
        return str(t.get("id") or t.get("task_id") or "") if isinstance(t, dict) else ""

    def title(t):
        if not isinstance(t, dict):
            return ""
        return str(t.get("title") or t.get("name") or (t.get("body") or "")[:60])

    n = {s: sum(1 for t in tasks if st(t) == s)
         for s in ("running", "done", "ready", "todo", "blocked", "failed")}
    cards = []
    for t in tasks:
        i, s = tid(t), st(t)
        cpu = (workers.get(i) or [""])[0]
        live = "\U0001F7E2 live" + (f" cpu {cpu}%" if cpu else "") if i in workers else ""
        log = tail_log(args.board, i) if s == "running" else ""
        cards.append(
            f'<div class="card"><b>{GLYPH.get(s, "\u2022")} {html.escape(title(t))}</b>'
            f'<span class="meta">{html.escape(i)} \u00b7 {html.escape(s)} {live}</span>'
            + (f'<div class="log">{html.escape(log)}</div>' if log else "") + "</div>")

    tiles = "".join(f'<div class="tile"><b>{v}</b><span>{k}</span></div>'
                     for k, v in {**n, **counts}.items())
    stamp = time.strftime("%d %b %Y \u00b7 %H:%M:%S IST")
    head = html.escape(args.title or f"{args.board} swarm \u2014 live")
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="10"><title>{head}</title><style>
 body{{font:14px/1.45 -apple-system,system-ui,sans-serif;color:var(--foreground,#1a1a1a);}}
 h1{{font-size:16px;margin:0 0 8px}} .grid{{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}}
 .tile,.card{{border:1px solid var(--border,#ddd);border-radius:10px;padding:8px 10px;min-width:120px}}
 .tile b{{font-size:20px;display:block}} .tile span,.meta{{color:var(--muted-foreground,#666);font-size:12px}}
 .card{{min-width:340px;margin:4px 0}} .card b{{display:block}}
 .log{{color:var(--muted-foreground,#666);font:11px/1.4 ui-monospace,monospace;margin-top:4px;word-break:break-all}}
</style></head><body>
<h1>{head}</h1><div class="meta">updated {stamp} \u00b7 auto-refresh 10s</div>
<div class="grid">{tiles}</div>{''.join(cards)}
</body></html>"""
    out = root / "army_dashboard.html"
    out.write_text(doc, encoding="utf-8")
    print(out)
    print(f"board={args.board} tasks={len(tasks)} live={len(workers)} "
          f"done={n['done']} running={n['running']} artifacts={counts}")

if __name__ == "__main__":
    main()
