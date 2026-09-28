#!/usr/bin/env python3
"""Search opencode conversation history stored in opencode.db (read-only).

Examples:
  python3 search_history.py --list                          # recent sessions
  python3 search_history.py --list -q 数据集 --dir ~/projects
  python3 search_history.py -q "旧树覆盖" --session mighty-panda
  python3 search_history.py -q "迁移方案" --since 2026-09-01
  python3 search_history.py --show mighty-panda --full
  python3 search_history.py -q 关键词 --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

SEARCHABLE_PARTS = ("text", "reasoning", "tool", "patch")
DEFAULT_MAX_CHARS = 500


def find_db(cli_db):
    if cli_db:
        return cli_db
    env = os.environ.get("OPENCODE_DB")
    if env:
        return env
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    cands = [
        os.path.join(xdg, "opencode", "opencode.db"),
        os.path.expanduser("~/.local/share/opencode/opencode.db"),
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    sys.exit("error: opencode.db not found (looked in: %s). Pass --db PATH." % ", ".join(cands))


def connect(db):
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=10)
    except sqlite3.OperationalError as e:
        sys.exit("error: cannot open %s: %s" % (db, e))
    con.execute("PRAGMA busy_timeout = 10000")
    con.execute("PRAGMA cache_size = -65536")  # 64 MB page cache
    return con


def esc_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_time_ms(s):
    s = s.strip()
    for f in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d"):
        try:
            return int(datetime.strptime(s, f).timestamp() * 1000)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return int(dt.timestamp() * 1000)
    except ValueError:
        sys.exit("error: cannot parse date %r (use YYYY-MM-DD or 'YYYY-MM-DD HH:MM')" % s)


def ts(ms):
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "?"


def truncate(s, n):
    s = s.replace("\r", "")
    if len(s) <= n:
        return s
    return s[:n] + " …[+%d chars]" % (len(s) - n)


def session_candidates(cur, ref):
    """Resolve a session reference (id / prefix / slug / title substring)."""
    rows = []
    cur.execute("SELECT id, slug, title, directory, time_updated FROM session WHERE id = ?", (ref,))
    r = cur.fetchone()
    if r:
        return [r]
    cur.execute("SELECT id, slug, title, directory, time_updated FROM session WHERE id LIKE ? ORDER BY time_updated DESC LIMIT 2", (ref + "%",))
    rs = cur.fetchall()
    if len(rs) == 1:
        return rs
    cur.execute("SELECT id, slug, title, directory, time_updated FROM session WHERE slug = ?", (ref,))
    r = cur.fetchone()
    if r:
        return [r]
    cur.execute(
        "SELECT id, slug, title, directory, time_updated FROM session "
        "WHERE slug LIKE ? OR title LIKE ? OR id LIKE ? ORDER BY time_updated DESC LIMIT 20",
        (ref + "%", "%" + ref + "%", ref + "%"),
    )
    rs = cur.fetchall()
    if not rs:
        sys.exit("error: no session matching %r (try --list to see sessions)" % ref)
    seen, out = set(), []
    for row in rs:
        if row[0] not in seen:
            seen.add(row[0])
            out.append(row)
    return out


def print_session_table(rows, label):
    print("%s (%d):" % (label, len(rows)))
    for sid, slug, title, directory, t_updated in rows:
        print("  %s  %s  %s" % (ts(t_updated), sid, slug))
        print("       title: %s" % title)
        print("       dir:   %s" % directory)


def fetch_transcript(con, session_id):
    """All messages (with parsed parts) of one session, in time order."""
    cur = con.cursor()
    cur.execute(
        """
        SELECT m.id, m.time_created,
               json_extract(m.data, '$.role'),
               (json_extract(m.data, '$.summary') IS NOT NULL),
               p.data
        FROM message m
        LEFT JOIN part p ON p.message_id = m.id
        WHERE m.session_id = ?
        ORDER BY m.time_created, p.time_created
        """,
        (session_id,),
    )
    msgs, order = {}, []
    for mid, tcre, role, has_sum, pdata in cur.fetchall():
        m = msgs.get(mid)
        if m is None:
            m = {"id": mid, "time": tcre, "role": role or "?", "summary": bool(has_sum), "parts": []}
            msgs[mid] = m
            order.append(mid)
        if pdata:
            try:
                m["parts"].append(json.loads(pdata))
            except (ValueError, TypeError):
                pass
    return [msgs[mid] for mid in order]


def render_part_text(pdata, max_chars, full):
    t = pdata.get("type")
    if t == "text":
        return pdata.get("text", "")
    if t == "reasoning":
        if not full:
            return "(thinking: %s)" % truncate(pdata.get("text", "").strip(), 120)
        return "(thinking)\n" + pdata.get("text", "")
    if t == "tool":
        state = pdata.get("state") or {}
        name = pdata.get("tool", "?")
        inp = state.get("input")
        if isinstance(inp, (dict, list)):
            inp_s = json.dumps(inp, ensure_ascii=False)
        else:
            inp_s = "" if inp is None else str(inp)
        lines = ["[tool:%s] %s" % (name, truncate(inp_s, 200 if not full else 1000))]
        if full:
            out = state.get("output")
            if out:
                lines.append("  output: %s" % truncate(str(out), max(4000, max_chars)))
            status = state.get("status")
            if status and status != "completed":
                lines.append("  status: %s" % status)
        return "\n".join(lines)
    if t == "patch":
        return "[patch %s]" % (pdata.get("file") or "?")
    if t == "file":
        return "[attachment %s]" % (pdata.get("filename") or "?")
    if t == "compaction":
        return "[compaction point — older context was summarized here]"
    if t == "subtask":
        return "[subtask]"
    return ""  # step-start / step-finish


def highlight(text, terms):
    if not terms or not text:
        return text
    try:
        pat = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
    except re.error:
        return text
    return pat.sub(lambda m: "**%s**" % m.group(0), text)


def render_message(m, max_chars, full, terms, mark=False):
    lines = []
    parts = []
    for p in m["parts"]:
        rendered = render_part_text(p, max_chars, full)
        if rendered:
            parts.append(highlight(rendered, terms))
    body = "\n".join(parts)
    if m.get("summary"):
        body = "[compaction summary]\n" + body
    suffix = "  << match" if mark else ""
    lines.append("%s  %s%s" % (ts(m["time"]), m["role"], suffix))
    if body:
        lines.append("    " + body.replace("\n", "\n    "))
    return "\n".join(lines)


def run_list(con, args):
    cur = con.cursor()
    where, params = ["s.time_archived IS NULL"], []
    if args.dir:
        where.append("(s.directory = ? OR s.directory LIKE ?)")
        params += [args.dir, args.dir.rstrip("/") + "/%"]
    if args.query:
        terms = [t.strip() for t in args.query.split() if t.strip()]
        for term in terms:
            where.append("(s.title LIKE ? ESCAPE '\\' OR s.slug LIKE ? ESCAPE '\\')")
            params += ["%" + esc_like(term) + "%", "%" + esc_like(term) + "%"]
    sql = """
        SELECT s.id, s.slug, s.title, s.directory, s.time_updated, COUNT(m.id)
        FROM session s
        LEFT JOIN message m ON m.session_id = s.id
        WHERE %s
        GROUP BY s.id
        ORDER BY s.time_updated DESC
        LIMIT ?
    """ % " AND ".join(where)
    cur.execute(sql, params + [args.limit])
    rows = cur.fetchall()
    if args.json:
        print(json.dumps(
            [{"id": r[0], "slug": r[1], "title": r[2], "directory": r[3],
              "updated": ts(r[4]), "messages": r[5]} for r in rows],
            ensure_ascii=False, indent=2))
        return
    if not rows:
        print("no sessions found")
        return
    print("%-20s %-16s %-5s %s" % ("UPDATED", "SLUG", "MSGS", "TITLE / DIR"))
    for sid, slug, title, directory, t_updated, nmsg in rows:
        print("%s  %-16s %-5d %s" % (ts(t_updated), slug, nmsg, title))
        print("%-20s %-16s %-5s %s" % ("", "", "", directory))


def run_show(con, args, session_rows):
    if len(session_rows) > 1:
        print_session_table(session_rows, "ambiguous --session %r" % args.session)
        sys.exit(2)
    (sid, slug, title, directory, t_updated) = session_rows[0]
    terms = [t.lower() for t in args.query.split()] if args.query else []
    transcript = fetch_transcript(con, sid)
    if args.json:
        out = {"session": {"id": sid, "slug": slug, "title": title, "directory": directory},
               "messages": [
                   {"id": m["id"], "time": ts(m["time"]), "role": m["role"],
                    "summary": m.get("summary", False),
                    "parts": [p for p in m["parts"]]}
                   for m in transcript]}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    print("session: %s  %s" % (slug, title))
    print("id:      %s" % sid)
    print("dir:     %s" % directory)
    print("updated: %s   (%d messages)" % (ts(t_updated), len(transcript)))
    print("-" * 70)
    for m in transcript:
        if not args.full and m["role"] not in ("user", "assistant"):
            continue
        if m["role"] == "assistant" and not args.full:
            # in non-full mode, keep text + tool call lines, drop reasoning
            m2 = dict(m)
            m2["parts"] = [p for p in m["parts"] if p.get("type") in ("text", "tool", "patch", "compaction")]
            m = m2
        if not m.get("parts") and not m.get("summary"):
            continue
        print()
        print(render_message(m, args.max_chars, args.full, terms))


def run_search(con, args, session_rows):
    if len(session_rows) > 1:
        print_session_table(session_rows, "ambiguous --session %r" % args.session)
        sys.exit(2)
    sid_filter = [session_rows[0][0]] if session_rows else None
    terms = [t.lower() for t in args.query.split()]
    kind = [k.strip() for k in args.kind.split(",") if k.strip()]
    bad = [k for k in kind if k not in SEARCHABLE_PARTS]
    if bad:
        sys.exit("error: unknown part kind(s): %s (choose from %s)" % (", ".join(bad), ", ".join(SEARCHABLE_PARTS)))

    where, params = [], []
    for term in terms:
        where.append("p.data LIKE ? ESCAPE '\\'")
        params.append("%" + esc_like(term) + "%")
    where.append("json_extract(p.data, '$.type') IN (%s)" % ",".join("?" * len(kind)))
    params += kind
    if args.since:
        where.append("m.time_created >= ?")
        params.append(parse_time_ms(args.since))
    if args.until:
        where.append("m.time_created <= ?")
        params.append(parse_time_ms(args.until))
    if sid_filter:
        where.append("m.session_id = ?")
        params.append(sid_filter[0])
    elif args.dir:
        where.append("(s.directory = ? OR s.directory LIKE ?)")
        params += [args.dir, args.dir.rstrip("/") + "/%"]

    cur = con.cursor()
    sql = """
        SELECT m.session_id, m.id, m.time_created,
               json_extract(m.data, '$.role') AS role,
               (json_extract(m.data, '$.summary') IS NOT NULL) AS has_summary,
               p.data
        FROM part p
        JOIN message m ON m.id = p.message_id
        LEFT JOIN session s ON s.id = m.session_id
        WHERE %s
        ORDER BY m.time_created
    """ % " AND ".join(where)
    cur.execute(sql, params)

    # group matched parts by (session, message)
    by_session = {}
    for sess_id, mid, tcre, role, has_sum, pdata in cur.fetchall():
        try:
            part = json.loads(pdata)
        except (ValueError, TypeError):
            continue
        d = by_session.setdefault(sess_id, {})
        m = d.get(mid)
        if m is None:
            m = {"id": mid, "time": tcre, "role": role or "?", "summary": bool(has_sum), "parts": []}
            d[mid] = m
        m["parts"].append(part)

    # cap total matched messages
    matched = []  # (time, session_id, msg)
    for sess_id, d in by_session.items():
        for m in d.values():
            matched.append((m["time"], sess_id, m))
    matched.sort()
    total = len(matched)
    if total > args.limit:
        matched = matched[: args.limit]

    if args.json:
        info = session_info(con, list(by_session.keys()))
        out = []
        for tcre, sess_id, m in matched:
            out.append({
                "session": info.get(sess_id, {"id": sess_id}),
                "message": {"id": m["id"], "time": ts(m["time"]), "role": m["role"],
                            "summary": m.get("summary", False), "parts": m["parts"]},
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    if not matched:
        print("no matches for %r" % args.query)
        return

    info = session_info(con, list(by_session.keys()))
    print("found %d matched message(s)%s across %d session(s)\n"
          % (total, " (showing first %d)" % args.limit if total > args.limit else "", len(by_session)))

    # fetch full transcripts for context
    transcripts = {sid: fetch_transcript(con, sid) for sid in by_session}
    idx = {sid: {m["id"]: i for i, m in enumerate(t)} for sid, t in transcripts.items()}

    n = 0
    for tcre, sess_id, m in matched:
        n += 1
        sinfo = info.get(sess_id, {})
        t = transcripts[sess_id]
        i = idx[sess_id].get(m["id"])
        if i is None:
            continue
        lo, hi = max(0, i - args.context), min(len(t), i + args.context + 1)
        print("=" * 78)
        print("match %d/%d  session %s %r" % (n, min(total, args.limit), sinfo.get("slug", sess_id), sinfo.get("title", "")))
        print("  dir: %s" % sinfo.get("directory", ""))
        if lo > 0:
            print("  [… %d earlier message(s) omitted …]" % lo)
        for j in range(lo, hi):
            msg = t[j]
            if j != i and not args.full:
                # keep context compact: text only
                mm = dict(msg)
                mm["parts"] = [p for p in msg["parts"] if p.get("type") == "text"]
                msg = mm
            print(render_message(msg, args.max_chars, args.full, [x.lower() for x in args.query.split()], mark=(j == i)))
        if hi < len(t):
            print("  [… %d later message(s) omitted …]" % (len(t) - hi))
        print()


def session_info(con, session_ids):
    info = {}
    if not session_ids:
        return info
    cur = con.cursor()
    q = "SELECT id, slug, title, directory, time_updated FROM session WHERE id IN (%s)" % ",".join("?" * len(session_ids))
    for sid, slug, title, directory, t_updated in cur.execute(q, session_ids):
        info[sid] = {"id": sid, "slug": slug, "title": title, "directory": directory, "updated": ts(t_updated)}
    return info


def main():
    ap = argparse.ArgumentParser(description="Search opencode conversation history (read-only).")
    ap.add_argument("--db", help="path to opencode.db (default: auto-detect)")
    ap.add_argument("-q", "--query", help="keyword(s); space-separated terms are ANDed")
    ap.add_argument("--session", help="restrict to one session (id, id prefix, slug, or title substring)")
    ap.add_argument("--dir", help="restrict to sessions whose directory equals or is under this path")
    ap.add_argument("--since", help="only messages at/after this date (YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")
    ap.add_argument("--until", help="only messages at/before this date")
    ap.add_argument("--kind", default=",".join(SEARCHABLE_PARTS),
                    help="comma-separated part kinds to search (default: %s)" % ",".join(SEARCHABLE_PARTS))
    ap.add_argument("--context", type=int, default=1, help="messages of context around each match (default 1)")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                    help="truncate each rendered part to N chars (default %d)" % DEFAULT_MAX_CHARS)
    ap.add_argument("--full", action="store_true", help="include reasoning + full tool output (with --show/--context)")
    ap.add_argument("--limit", type=int, default=25, help="max sessions (--list) or matched messages (--query)")
    ap.add_argument("--list", action="store_true", help="list recent sessions instead of searching")
    ap.add_argument("--show", nargs="?", const=True, default=None, metavar="SESSION",
                    help="dump full transcript of a session: '--show slug' or '--show --session slug'")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    show_ref = args.show if isinstance(args.show, str) else args.session
    if args.show and not show_ref:
        sys.exit("error: --show needs a session ref: '--show slug' or '--show --session slug'")
    if not args.list and not args.show and not args.query:
        sys.exit("error: give a -q/--query, --list, or --show")

    db = find_db(args.db)
    con = connect(db)
    try:
        cur = con.cursor()
        if args.list:
            run_list(con, args)
            return
        session_rows = None
        if show_ref:
            session_rows = session_candidates(cur, show_ref)
        if args.show:
            run_show(con, args, session_rows or [])
            return
        run_search(con, args, session_rows or [])
    finally:
        con.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)