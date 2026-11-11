"""Explain where your Claude Code / Codex token usage went, from local logs."""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Input-equivalent weights from Anthropic API price ratios (cache read 0.1x,
# 5m cache write 1.25x, 1h cache write 2x, output 5x). A ranking aid, not dollars.
WEIGHTS = {"input": 1.0, "cache_write_5m": 1.25, "cache_write_1h": 2.0,
           "cache_read": 0.1, "output": 5.0}
FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def long_path(p: Path) -> str:
    # Claude Code nests subagent logs deep enough to pass Windows' 260-char limit.
    s = str(p.resolve())
    if os.name == "nt" and not s.startswith("\\\\?\\"):
        s = "\\\\?\\" + s
    return s


def parse_since(text: str | None) -> datetime | None:
    if not text:
        return None
    m = re.fullmatch(r"(\d+)([mhd])", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"bad --since {text!r}, use e.g. 30m, 5h, 7d")
    n, unit = int(m[1]), m[2]
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]
    return datetime.now(timezone.utc) - delta


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def weighted(t: dict) -> float:
    return sum(t.get(k, 0) * w for k, w in WEIGHTS.items())


def claude_usage(u: dict) -> dict:
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    w5m = cc.get("ephemeral_5m_input_tokens")
    if w5m is None:  # older logs only have the flat field
        w5m = (u.get("cache_creation_input_tokens", 0) or 0) - w1h
    return {"input": u.get("input_tokens", 0) or 0, "cache_write_5m": w5m,
            "cache_write_1h": w1h, "cache_read": u.get("cache_read_input_tokens", 0) or 0,
            "output": u.get("output_tokens", 0) or 0}


def read_jsonl(path: Path, needle: str | None = None):
    try:
        with open(long_path(path), encoding="utf-8", errors="replace") as f:
            for line in f:
                if needle and needle not in line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        print(f"skip {path}: {e}", file=sys.stderr)


def stale(path: Path, since: datetime | None) -> bool:
    # A log not written to since the cutoff can't hold newer entries.
    if not since:
        return False
    try:
        return os.stat(long_path(path)).st_mtime < since.timestamp()
    except OSError:
        return True


def scan_claude(root: Path, since: datetime | None):
    """Yield one event per billed assistant message."""
    seen = set()
    for path in root.rglob("*.jsonl"):
        if stale(path, since):
            continue
        project_dir = path.relative_to(root).parts[0]
        agent_type = None
        if path.parent.name == "subagents":
            meta = path.with_name(path.name[:-len(".jsonl")] + ".meta.json")
            try:
                with open(long_path(meta), encoding="utf-8") as f:
                    agent_type = json.load(f).get("agentType")
            except (OSError, ValueError):
                pass
        for d in read_jsonl(path, '"usage"'):
            if d.get("type") != "assistant":
                continue
            msg = d.get("message") or {}
            u = msg.get("usage")
            if not u:
                continue
            # Streaming writes one line per content block with the same usage.
            key = (msg.get("id"), d.get("requestId"))
            if key[0] and key in seen:
                continue
            seen.add(key)
            ts = parse_ts(d.get("timestamp"))
            if since and (ts is None or ts < since):
                continue
            cwd = d.get("cwd")
            sidechain = bool(d.get("isSidechain")) or agent_type is not None
            yield {"tool": "claude", "ts": ts, "session": d.get("sessionId") or path.stem,
                   "project": Path(cwd).name if cwd else project_dir,
                   "sidechain": sidechain,
                   "agent": (agent_type or "subagent") if sidechain else None,
                   "model": msg.get("model"), "tokens": claude_usage(u)}


def scan_codex(root: Path, since: datetime | None):
    """Codex logs a running total per session; emit the delta of each token_count event."""
    for path in root.rglob("*.jsonl"):
        if stale(path, since):
            continue
        project, prev = None, {}
        for d in read_jsonl(path):
            p = d.get("payload") or {}
            if d.get("type") == "session_meta" and p.get("cwd"):
                project = Path(p["cwd"]).name
            if d.get("type") != "event_msg" or p.get("type") != "token_count":
                continue
            tot = ((p.get("info") or {}).get("total_token_usage")) or {}
            if not tot:
                continue
            cur = {"cached": tot.get("cached_input_tokens", 0) or 0,
                   "input": tot.get("input_tokens", 0) or 0,
                   "output": tot.get("output_tokens", 0) or 0}
            delta = {k: cur[k] - prev.get(k, 0) for k in cur}
            prev = cur
            if not any(delta.values()):
                continue
            ts = parse_ts(d.get("timestamp"))
            if since and (ts is None or ts < since):
                continue
            # Codex counts cached tokens inside input_tokens.
            yield {"tool": "codex", "ts": ts, "session": path.stem,
                   "project": project or "?", "sidechain": False, "agent": None,
                   "model": None,
                   "tokens": {"input": delta["input"] - delta["cached"], "cache_write_5m": 0,
                              "cache_write_1h": 0, "cache_read": delta["cached"],
                              "output": delta["output"]}}


def add(acc: dict, t: dict):
    for k in FIELDS:
        acc[k] = acc.get(k, 0) + t[k]


def why(t: dict, turns: int) -> str:
    w = weighted(t) or 1
    parts = {k: t.get(k, 0) * WEIGHTS[k] / w for k in FIELDS}
    top = max(parts, key=parts.get)
    pct = round(parts[top] * 100)
    reasons = {
        "cache_read": f"re-reading a long context every turn ({turns} turns, avg {t['cache_read'] // max(turns, 1):,} cached tokens/turn)",
        "cache_write_1h": "writing large prompts to the 1h cache (new or changed context)",
        "cache_write_5m": "writing large prompts to the 5m cache (new or changed context)",
        "output": "long model output (code, thinking, reports)",
        "input": "uncached input",
    }
    return f"{pct}% {reasons[top]}"


def build_report(events: list, top: int = 5) -> dict:
    total, side = {}, {}
    projects, sessions, agents = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    turns = defaultdict(int)
    meta = {}
    for e in events:
        t = e["tokens"]
        add(total, t)
        add(projects[e["project"]], t)
        add(sessions[e["session"]], t)
        turns[e["session"]] += 1
        m = meta.setdefault(e["session"], {"project": e["project"], "tool": e["tool"],
                                          "first": e["ts"], "last": e["ts"]})
        if e["ts"]:
            m["first"] = min(filter(None, [m["first"], e["ts"]]))
            m["last"] = max(filter(None, [m["last"], e["ts"]]))
        if e["sidechain"]:
            add(side, t)
            add(agents[e["agent"]], t)

    tw = weighted(total) or 1

    def row(name, t, **extra):
        return {"name": name, "weighted": round(weighted(t)), "share": round(weighted(t) / tw, 4),
                **{k: t.get(k, 0) for k in FIELDS}, **extra}

    def ranked(d):
        return sorted(d.items(), key=lambda kv: weighted(kv[1]), reverse=True)

    iso = lambda x: x.isoformat() if x else None
    big = sorted(events, key=lambda e: weighted(e["tokens"]), reverse=True)[:top]
    return {
        "events": len(events),
        "total": row("total", total),
        "sidechain": row("sidechain", side),
        "projects": [row(k, v) for k, v in ranked(projects)[:top * 2]],
        "agents": [row(k, v) for k, v in ranked(agents)[:top]],
        "top_sessions": [row(k, v, project=meta[k]["project"], tool=meta[k]["tool"],
                             turns=turns[k], first=iso(meta[k]["first"]),
                             last=iso(meta[k]["last"]), why=why(v, turns[k]))
                         for k, v in ranked(sessions)[:top]],
        "top_events": [row(e["session"], e["tokens"], ts=iso(e["ts"]), project=e["project"],
                           model=e["model"], agent=e["agent"], why=why(e["tokens"], 1))
                       for e in big],
    }


def fmt(n: float) -> str:
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= div:
            return f"{n / div:.1f}{unit}"
    return str(round(n))


def print_text(r: dict):
    t = r["total"]
    print(f"{r['events']} billed messages, {fmt(t['weighted'])} input-equivalent tokens")
    print(f"  input {fmt(t['input'])}  cache write {fmt(t['cache_write_5m'] + t['cache_write_1h'])}"
          f"  cache read {fmt(t['cache_read'])}  output {fmt(t['output'])}")
    print(f"  subagents: {r['sidechain']['share']:.0%} of weighted usage")
    print("\nBy project:")
    for p in r["projects"]:
        print(f"  {p['share']:6.1%}  {fmt(p['weighted']):>7}  {p['name']}")
    if r["agents"]:
        print("\nBy subagent type:")
        for a in r["agents"]:
            print(f"  {a['share']:6.1%}  {fmt(a['weighted']):>7}  {a['name']}")
    print("\nMost expensive sessions:")
    for s in r["top_sessions"]:
        print(f"  {s['share']:6.1%}  {fmt(s['weighted']):>7}  {s['project']}  {s['name'][:8]}"
              f"  ({(s['last'] or '')[:16]})")
        print(f"          why: {s['why']}")
    print("\nMost expensive single messages:")
    for e in r["top_events"]:
        who = e["agent"] or "main"
        print(f"  {fmt(e['weighted']):>7}  {(e['ts'] or '')[:16]}  {e['project']}  {who}  {e['model'] or ''}")
        print(f"          why: {e['why']}")


def print_csv(report: dict):
    """Export session rows with their existing raw values, without new arithmetic."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", newline="")
    fields = ("session", "project", "tool", "weighted", "share", "input",
              "cache_write_5m", "cache_write_1h", "cache_read", "output", "why")
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for session in report["top_sessions"]:
        writer.writerow({**session, "session": session["name"]})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kota", description=__doc__)
    ap.add_argument("--since", type=parse_since, help="only the last N minutes/hours/days: 30m, 5h, 7d")
    output = ap.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="machine-readable output")
    output.add_argument("--csv", action="store_true", help="export the session table as UTF-8 CSV")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude" / "projects")
    ap.add_argument("--codex-dir", type=Path, default=Path.home() / ".codex" / "sessions")
    a = ap.parse_args(argv)

    events = []
    if a.claude_dir.is_dir():
        events += scan_claude(a.claude_dir, a.since)
    if a.codex_dir.is_dir():
        events += scan_codex(a.codex_dir, a.since)
    if not events:
        print("no usage found", file=sys.stderr)
        return 1
    r = build_report(events, a.top)
    if a.json:
        json.dump(r, sys.stdout, indent=2, ensure_ascii=False)
        print()
    elif a.csv:
        print_csv(r)
    else:
        print_text(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
