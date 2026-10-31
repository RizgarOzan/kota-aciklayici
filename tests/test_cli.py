import json
from datetime import datetime, timedelta, timezone

from kota.cli import build_report, main, parse_since, scan_claude, scan_codex

NOW = datetime.now(timezone.utc)


def ts(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


def assistant(msg_id, hours_ago, session="s1", sidechain=False, cache_read=0, out=10, w1h=0):
    return {"type": "assistant", "sessionId": session, "isSidechain": sidechain,
            "timestamp": ts(hours_ago), "cwd": "/home/me/proj", "requestId": "r" + msg_id,
            "message": {"id": msg_id, "model": "claude-x", "usage": {
                "input_tokens": 5, "output_tokens": out, "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": w1h,
                "cache_creation": {"ephemeral_1h_input_tokens": w1h, "ephemeral_5m_input_tokens": 0}}}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")


def make_claude(root):
    main_rows = [assistant("m1", 1, cache_read=100_000),
                 assistant("m1", 1, cache_read=100_000),  # streamed duplicate
                 assistant("m2", 2, out=1000),
                 assistant("old", 24 * 10, out=99_999),
                 {"type": "user", "message": {"content": "hi"}}]
    write(root / "C--proj" / "s1.jsonl", main_rows)
    sub = root / "C--proj" / "s1" / "subagents" / "agent-a1.jsonl"
    write(sub, [assistant("x1", 1, sidechain=True, w1h=50_000)])
    sub.with_name("agent-a1.meta.json").write_text('{"agentType": "Explore"}', encoding="utf-8")


def test_dedupes_streamed_lines_and_tags_subagents(tmp_path):
    make_claude(tmp_path)
    ev = list(scan_claude(tmp_path, None))
    assert sorted(e["tokens"]["output"] for e in ev) == [10, 10, 1000, 99_999]
    sub = [e for e in ev if e["sidechain"]]
    assert len(sub) == 1 and sub[0]["agent"] == "Explore"
    assert sub[0]["tokens"]["cache_write_1h"] == 50_000
    assert {e["project"] for e in ev} == {"proj"}


def test_since_drops_old_events(tmp_path):
    make_claude(tmp_path)
    ev = list(scan_claude(tmp_path, parse_since("5h")))
    assert len(ev) == 3 and all(e["tokens"]["output"] != 99_999 for e in ev)


def test_report_shares_and_why(tmp_path):
    make_claude(tmp_path)
    r = build_report(list(scan_claude(tmp_path, parse_since("5h"))))
    # weighted: m1 5+10*5+100000*0.1=10055, m2 5+5000=5005, x1 5+50+100000=100055
    assert r["total"]["weighted"] == 115_115
    assert r["sidechain"]["share"] == round(100_055 / 115_115, 4)
    assert r["agents"][0]["name"] == "Explore"
    assert "1h cache" in r["top_events"][0]["why"]
    assert "cached tokens/turn" in r["top_events"][1]["why"]


def test_codex_running_totals_become_deltas(tmp_path):
    def tc(hours_ago, inp, cached, out):
        return {"type": "event_msg", "timestamp": ts(hours_ago), "payload": {
            "type": "token_count", "info": {"total_token_usage": {
                "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}}}}
    write(tmp_path / "2026" / "11" / "01" / "rollout-a.jsonl",
          [{"type": "session_meta", "payload": {"cwd": "/w/repo"}},
           tc(2, 1000, 0, 100), tc(2, 1000, 0, 100), tc(1, 3000, 1500, 300)])
    ev = list(scan_codex(tmp_path, None))
    assert [e["tokens"] for e in ev] == [
        {"input": 1000, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 0, "output": 100},
        {"input": 500, "cache_write_5m": 0, "cache_write_1h": 0, "cache_read": 1500, "output": 200}]
    assert ev[0]["project"] == "repo"


def test_cli_json_and_bad_since(tmp_path, capsys):
    make_claude(tmp_path)
    assert main(["--json", "--since", "5h", "--claude-dir", str(tmp_path),
                 "--codex-dir", str(tmp_path / "none")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["events"] == 3 and out["top_sessions"][0]["name"] == "s1"
    try:
        parse_since("yesterday")
    except Exception as e:
        assert "bad --since" in str(e)
    else:
        raise AssertionError("expected error")
