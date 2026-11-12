# kota-aciklayici

A small CLI that reads your local Claude Code logs (and Codex logs, if you have them) and tells you where your tokens went and roughly why. "Kota açıklayıcı" is Turkish for "quota explainer".

I wrote it because I kept hitting my weekly limit and couldn't tell which session or subagent ate it.

## What it does

- Reads `~/.claude/projects/**/*.jsonl`, including subagent logs under `<session>/subagents/`, and `~/.codex/sessions/**/*.jsonl`.
- Counts each billed assistant message once (Claude Code writes the same usage on several streamed lines).
- Splits usage by project, by session, and by subagent type (Explore, general-purpose, your own agents).
- Lists the 5 most expensive sessions and single messages with a one-line reason, like "55% re-reading a long context every turn (347 turns, avg 127,158 cached tokens/turn)" or "100% writing large prompts to the 1h cache".
- `--since 5h` / `--since 7d` / `--since 30m`, `--json`, `--csv`, `--top N`, `--tool claude` / `--tool codex` to scan only one of them.

No dependencies, Python 3.11+. Nothing leaves your machine.

```
pip install .
kota --since 7d
kota --since 5h --json
kota --since 7d --csv --top 10 > sessions.csv
```

Or without installing: `python -m kota --since 7d`.

`--csv` exports the top session table as UTF-8 CSV with a header row. `--top`
limits its rows, and `--csv` cannot be combined with `--json`. Columns are
`session`, `project`, `tool`, `weighted`, `share`, `input`, `cache_write_5m`,
`cache_write_1h`, `cache_read`, `output`, and `why`. `session` is the existing
report's `name`; all values come directly from the report. Cache writes stay
separate, and `share` is the existing fraction (for example `0.25`, not `25%`).

## What it looked like on my machine

One week of my own logs (Nov 2026), trimmed:

```
14112 billed messages, 322.2M input-equivalent tokens
  input 132.7k  cache write 77.6M  cache read 1.3B  output 8.6M
  subagents: 13% of weighted usage

By subagent type:
   10.2%    33.0M  general-purpose
    0.3%   872.5k  web-fetch
    0.1%   443.9k  Explore

Most expensive sessions:
   11.8%    37.9M  Downloads  89e532e4  (2026-11-06T20:49)
          why: 41% re-reading a long context every turn (1303 turns, avg 119,982 cached tokens/turn)
   11.6%    37.4M  Downloads  4dda5b85  (2026-11-06T20:48)
          why: 49% re-reading a long context every turn (1443 turns, avg 127,110 cached tokens/turn)
    8.0%    25.8M  scratch-2026-11-02-675ce3  32587e7f  (2026-11-04T18:54)
          why: 55% re-reading a long context every turn (1049 turns, avg 135,224 cached tokens/turn)
```

Three sessions took 31% of the week. Each ran past a thousand turns and re-read about 125k tokens of context on every one of them. Starting fresh sessions earlier would have saved most of that.

## Numbers are weighted, not dollars

Rankings use "input-equivalent tokens": cache reads count 0.1x, 5-minute cache writes 1.25x, 1-hour cache writes 2x, output 5x, plain input 1x. Those are Anthropic's API price ratios. Subscription limits aren't published per token type, so treat the weights as a reasonable guess for "what costs more", not as your real quota math. Raw token counts are in the `--json` output if you want to weigh them differently.

## How it differs from ccusage

[ccusage](https://github.com/ryoppippi/ccusage) already exists, is more mature, and is what most people should try first. It gives daily, monthly, session and 5-hour block reports with dollar costs from live pricing data, and it has a Codex variant.

This tool is narrower. It cares about the "why": subagent share broken down by agent type, the single most expensive messages, and a short reason per session (context re-reads vs cache writes vs output). It doesn't do dollar costs, live block tracking or a statusline. If ccusage already answers your question, use it.

## Limits

- The Claude Code and Codex log formats aren't public APIs; a release can change them and break parsing.
- The Codex parser follows the `token_count` running totals in rollout files. I tested it on synthetic files only, since I don't have Codex logs on my machine.
- Projects are grouped by the folder name the session ran in. Two folders with the same name merge, and a repo opened from a subfolder shows up as two rows.
- On a few GB of logs a full scan takes a few minutes. `--since` skips files not modified since the cutoff, so recent windows are fast.

## License

MIT
