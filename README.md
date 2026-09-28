# opencode-history-search

[![skills.sh](https://skills.sh/b/huangcb01/opencode-history-search)](https://skills.sh/huangcb01/opencode-history-search)

An [opencode](https://opencode.ai) skill that searches your local conversation
history — including turns that are no longer visible in the current context
because of compaction.

Install via [skills.sh](https://skills.sh):

```sh
npx skills add huangcb01/opencode-history-search
```

## Background

opencode persists every session and message in a local SQLite database
(`opencode.db`). Compaction only summarizes old turns out of the model's
visible context; it never deletes stored history. This skill adds a read-only
CLI on top of that database so an agent (or you) can recall what was said,
decided, or done in past sessions — across projects.

## Install

Requires Python 3 (standard library only, no dependencies).

Option A — point opencode at this repository (keeps it git-updatable):

```jsonc
// ~/.config/opencode/opencode.jsonc
{
  "skills": {
    "paths": ["/path/to/opencode-history-search"]
  }
}
```

Option B — copy the skill into your personal skills directory:

```sh
git clone https://github.com/huangcb01/opencode-history-search \
  ~/.agents/skills/opencode-history-search
```

Restart opencode after installing.

## Usage

```sh
S=<path-to-skill>/scripts/search_history.py

# List recent sessions (filter by keyword or project dir)
python3 $S --list --query 关键词
python3 $S --list --dir /path/to/project --limit 20

# Search history (within one session, a project dir, or everywhere)
python3 $S -q 关键词 --session <slug或id>
python3 $S -q 关键词 --dir /path/to/project --since 2026-09-01

# Dump the full transcript of a session
python3 $S --show <slug或id>            # text + tool call lines
python3 $S --show <slug或id> --full     # + reasoning + tool outputs
```

Session references accept a full id (`ses_...`), a unique id prefix, a slug,
or a title substring. Multiple space-separated terms in `-q` are ANDed.
Add `--json` for machine-readable output.

See [SKILL.md](SKILL.md) for the full flag reference and performance notes.

## Notes

- Opens the database **read-only**; safe to run while opencode is active.
- DB location is auto-detected (`$XDG_DATA_HOME/opencode/opencode.db`,
  fallback `~/.local/share/opencode/opencode.db`); override with `--db` or
  `OPENCODE_DB`.
- Cross-session searches on very large databases can take tens of seconds —
  scope with `--session` / `--dir` / `--since` when possible.