---
name: opencode-history-search
description: Search opencode's local conversation history database (opencode.db) for past user messages, assistant replies, tool calls and tool outputs, including content that is no longer visible in the current context because of compaction (上下文压缩). Use when the user asks about 对话历史, 之前说过/讨论过/决定的/改过的, 压缩前, "what did we say/decide/do earlier", "recall that conversation", "which session discussed X", or wants to list/locate past sessions across projects.
---

# OpenCode History Search

opencode persists every session and message in a local SQLite database
(`opencode.db`). After compaction drops old turns from the visible context,
they are still fully queryable here — compaction never deletes stored history.

## Script location

The script lives at `scripts/search_history.py` inside this skill's directory
(the directory containing this SKILL.md). Invoke it as:

```
python3 <skill_dir>/scripts/search_history.py [options]
```

It only uses the Python 3 standard library and opens the DB **read-only**, so it
is safe to run while opencode is running. DB path is auto-detected
(`$XDG_DATA_HOME/opencode/opencode.db`, fallback `~/.local/share/opencode/opencode.db`);
override with `--db PATH` or the `OPENCODE_DB` env var.

## Typical workflow

1. Locate the session (when the user doesn't name it):

   ```
   python3 .../search_history.py --list --query 关键词
   python3 .../search_history.py --list --dir /path/to/project --limit 20
   ```

2. Search within that session, or across all sessions:

   ```
   python3 .../search_history.py -q 关键词 --session <slug或id>
   python3 .../search_history.py -q 关键词 --dir /path/to/project --since 2026-09-01
   ```

   Multiple space-separated terms in `-q` are ANDed; matching is
   case-insensitive for ASCII and covers text, reasoning, tool inputs and
   tool outputs by default.

3. Dump the whole transcript of a session when the user wants to review it:

   ```
   python3 .../search_history.py --show <slug或id>            # text + tool call lines
   python3 .../search_history.py --show <slug或id> --full     # + reasoning + tool outputs
   ```

Session references accept a full id (`ses_...`), a unique id prefix, a slug
(`mighty-panda`), or a title substring. Ambiguous references print the
candidates (exit code 2) — pick one or ask the user.

## Flag reference

| flag | meaning |
| --- | --- |
| `-q, --query TEXT` | keyword search (space-separated terms ANDed) |
| `--session REF` | restrict to one session (id / prefix / slug / title substring) |
| `--dir PATH` | restrict to sessions in this directory (or subdirectories) |
| `--since / --until DATE` | date range filter (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM`) |
| `--kind K1,K2` | part kinds to search: `text,reasoning,tool,patch` (default all four) |
| `--context N` | N surrounding messages per match (default 1) |
| `--full` | include reasoning and full tool outputs |
| `--max-chars N` | truncate each rendered part to N chars (default 500) |
| `--limit N` | max matched messages (default 25) / max sessions for `--list` |
| `--list` | list recent sessions, optional `--query` / `--dir` filters |
| `--show REF` | print full transcript of a session |
| `--json` | machine-readable JSON output |
| `--db PATH` | explicit DB path |

## Performance

- `--list`, `--show`, and searches scoped to one session are fast (seconds).
- A cross-session search with no `--session`/`--dir`/`--since` scans the whole
  history and can take tens of seconds on large databases — scope it down
  first when possible.

## Answering the user

- Base the answer only on what the script returned; quote or paraphrase the
  matched turns, noting the session slug, title, and timestamp.
- If there are no matches, say so — do not guess or reconstruct history
  from memory of the visible context alone.
- When a match is a compaction summary (marked `[compaction summary]`), treat
  it as a summary of older turns, not verbatim history.