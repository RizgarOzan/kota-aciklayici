# kota-aciklayici

A small CLI that reads your local Claude Code logs (and Codex logs, if you have them) and tells you where your tokens went and roughly why. "Kota açıklayıcı" is Turkish for "quota explainer".

I wrote it because I kept hitting my weekly limit and couldn't tell which session or subagent ate it.

## What it does

- Reads `~/.claude/projects/**/*.jsonl`, including subagent logs under `<session>/subagents/`, and `~/.codex/sessions/**/*.jsonl`.
- Counts each billed assistant message once (Claude Code writes the same usage on several streamed lines).
- Splits usage by project, by session, and by subagent type (Explore, general-purpose, your own agents).
- Lists the 5 most expensive sessions and single messages with a one-line reason, like "55% re-reading a long context every turn (347 turns, avg 127,158 cached tokens/turn)" or "100% writing large prompts to the 1h cache".
- `--since 5h` / `--since 7d` / `--since 30m`, `--json`, `--top N`.

No dependencies, Python 3.11+. Nothing leaves your machine.

```
pip install .
kota --since 7d
kota --since 5h --json
```

Or without installing: `python -m kota --since 7d`.

## Numbers are weighted, not dollars

Rankings use "input-equivalent tokens": cache reads count 0.1x, 5-minute cache writes 1.25x, 1-hour cache writes 2x, output 5x, plain input 1x. Those are Anthropic's API price ratios. Subscription limits aren't published per token type, so treat the weights as a reasonable guess for "what costs more", not as your real quota math. Raw token counts are in the `--json` output if you want to weigh them differently.

## How it differs from ccusage

[ccusage](https://github.com/ryoppippi/ccusage) already exists, is more mature, and is what most people should try first. It gives daily, monthly, session and 5-hour block reports with dollar costs from live pricing data, and it has a Codex variant.

This tool is narrower. It cares about the "why": subagent share broken down by agent type, the single most expensive messages, and a short reason per session (context re-reads vs cache writes vs output). It doesn't do dollar costs, live block tracking or a statusline. If ccusage already answers your question, use it.

## Limits

- The Claude Code and Codex log formats aren't public APIs; a release can change them and break parsing.
- The Codex parser follows the `token_count` running totals in rollout files. I tested it on synthetic files only, since I don't have Codex logs on my machine.
- On a few GB of logs a full scan takes a few minutes. `--since` skips files not modified since the cutoff, so recent windows are fast.

## License

MIT
