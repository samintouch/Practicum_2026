"""
export_chat_log.py -- convert a Claude Code session's raw .jsonl transcript
into a clean, human-readable Markdown (or HTML) file.

Claude Code saves every session automatically as a JSONL file under
~/.claude/projects/<project-slug>/<session-id>.jsonl -- one JSON object per
line, covering not just the visible back-and-forth but internal bookkeeping
too (tool calls/results, IDE attachments, thinking blocks, queue metadata,
etc). This script reads that file and produces a readable transcript: your
own messages and Claude's replies in full, tool calls/results summarized to
one line each (unless --full is passed), and internal noise (system-
reminder tags, IDE attachment tags, thinking blocks) stripped out by
default -- that noise is real content the harness injects automatically,
not something either of you actually typed.

Usage:
    python export_chat_log.py                    # auto-detect the most
                                                   # recently modified session
                                                   # across ALL projects,
                                                   # write .md next to it
    python export_chat_log.py path/to/session.jsonl
    python export_chat_log.py --out chat.md
    python export_chat_log.py --format html --out chat.html
    python export_chat_log.py --full              # complete tool inputs/
                                                    # outputs, not summaries
    python export_chat_log.py --include-thinking   # also include extended-
                                                    # thinking text (usually
                                                    # empty/redacted in the
                                                    # saved log already)
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

# Auto-injected context Claude Code appends to what you actually typed --
# not part of what either of you wrote, so stripped from user turns by
# default. (?s) so a tag's content can span multiple lines.
_SYSTEM_TAG_RE = re.compile(
    r"<(system-reminder|ide_opened_file|ide_selection|user-prompt-submit-hook)\b[^>]*>.*?</\1>",
    re.S,
)


def find_latest_session():
    root = Path.home() / ".claude" / "projects"
    candidates = sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"No session .jsonl files found under {root}")
    return candidates[0]


def clean_user_text(text):
    return _SYSTEM_TAG_RE.sub("", text).strip()


def summarize_tool_use(block):
    name = block.get("name", "?")
    inp = block.get("input", {}) or {}
    bits = []
    for key in ("file_path", "command", "pattern", "path", "url", "query", "prompt", "description", "skill"):
        if inp.get(key):
            val = str(inp[key])
            if len(val) > 140:
                val = val[:140] + "…"
            bits.append(f"{key}={val!r}")
    detail = ", ".join(bits) if bits else (json.dumps(inp)[:160] if inp else "")
    return f"\U0001f527 **{name}**" + (f" — {detail}" if detail else "")


def summarize_tool_result(block, max_chars):
    content = block.get("content", "")
    if isinstance(content, list):
        content = "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        )
    content = str(content)
    label = "⚠️ tool error" if block.get("is_error") else "→ result"
    if not content.strip():
        return label
    if len(content) > max_chars:
        content = content[:max_chars].rstrip() + f"\n… [{len(content) - max_chars} more characters truncated]"
    return f"{label}:\n```\n{content}\n```"


def render_entry(role, blocks, include_thinking, full_tool_output, max_chars):
    parts = []
    result_limit = 10**9 if full_tool_output else max_chars
    for block in blocks:
        if isinstance(block, str):
            text = clean_user_text(block) if role == "user" else block
            if text:
                parts.append(text)
            continue
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text = block.get("text", "")
            if role == "user":
                text = clean_user_text(text)
            if text.strip():
                parts.append(text)
        elif btype == "thinking":
            if include_thinking and block.get("thinking", "").strip():
                quoted = block["thinking"].replace("\n", "\n> ")
                parts.append(f"_(thinking)_\n> {quoted}")
        elif btype == "tool_use":
            parts.append(summarize_tool_use(block))
        elif btype == "tool_result":
            parts.append(summarize_tool_result(block, result_limit))
        elif btype == "image":
            parts.append("_[image attached]_")
    return "\n\n".join(p for p in parts if p and p.strip())


def convert(session_path, include_thinking=False, full_tool_output=False, max_chars=1200):
    entries = []
    with open(session_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("type") not in ("user", "assistant"):
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            blocks = content if isinstance(content, list) else ([content] if content else [])
            role = msg.get("role", d["type"])
            # A "user" turn covers three very different things in the raw
            # log, distinguished by `origin.kind` (not by guessing from the
            # content's shape): a real typed message ("human"), the harness
            # notifying Claude a background task finished
            # ("task-notification", e.g. a Bash run_in_background result --
            # never something you typed), or a tool's output being fed back
            # after Claude called it (no `origin` field at all -- verified
            # by also checking for tool_result blocks, since a genuine user
            # turn and a tool-result turn never mix in one message).
            origin_kind = (d.get("origin") or {}).get("kind") if isinstance(d.get("origin"), dict) else None
            has_tool_result = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
            if role == "assistant":
                display_role = "assistant"
            elif origin_kind == "human":
                display_role = "user"
            elif origin_kind == "task-notification":
                display_role = "task"
            elif has_tool_result:
                display_role = "tool"
            else:
                # anything else (a hook message, a compacted-history marker,
                # etc.) -- shown, but never silently mislabeled as "You"
                display_role = "system"
            body = render_entry(role, blocks, include_thinking, full_tool_output, max_chars)
            if not body:
                continue
            entries.append((display_role, d.get("timestamp"), body))
    return entries


def write_markdown(entries, out_path, session_path):
    lines = [
        "# Claude Code session transcript",
        "",
        f"Source: `{session_path}`  ",
        f"{len(entries)} message(s). Tool calls are summarized to one line each here -- "
        "re-run with `--full` for complete tool inputs/outputs.",
        "",
        "---",
        "",
    ]
    speakers = {
        "user": "\U0001f9d1 You", "assistant": "\U0001f916 Claude",
        "tool": "\U0001f527 Tool result", "task": "\U0001f4cb Background task",
        "system": "⚙️ System",
    }
    for role, ts, body in entries:
        speaker = speakers.get(role, role)
        header = f"## {speaker}"
        if ts:
            header += f"  \n`{ts}`"
        lines += [header, "", body, "", "---", ""]
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_html(entries, out_path, session_path):
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Claude Code session transcript</title>",
        "<style>",
        "body{font-family:system-ui,'Segoe UI',Arial,sans-serif;max-width:900px;margin:2rem auto;"
        "padding:0 1rem;line-height:1.55;color:#1b2321;background:#f7f7f5}",
        ".msg{border:1px solid #ddd;border-radius:10px;padding:14px 18px;margin-bottom:14px;background:#fff}",
        ".msg.user{border-left:4px solid #2b5fa8}",
        ".msg.assistant{border-left:4px solid #0c6b63}",
        ".msg.tool{border-left:4px solid #999;background:#fafafa}",
        ".msg.task{border-left:4px solid #b98900;background:#fffdf5}",
        ".msg.system{border-left:4px solid #999;background:#fafafa}",
        ".who{font-weight:600;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em;color:#666}",
        ".ts{font-size:.75rem;color:#999;margin-left:8px}",
        "pre{background:#eee;padding:10px;border-radius:6px;overflow-x:auto;white-space:pre-wrap;"
        "font-family:inherit;margin:8px 0 0}",
        "</style></head><body>",
        "<h1>Claude Code session transcript</h1>",
        f"<p>Source: <code>{html.escape(str(session_path))}</code> &middot; {len(entries)} message(s)</p>",
    ]
    who_labels = {"user": "You", "assistant": "Claude", "tool": "Tool result",
                  "task": "Background task", "system": "System"}
    for role, ts, body in entries:
        who = who_labels.get(role, role)
        ts_html = f"<span class='ts'>{html.escape(ts)}</span>" if ts else ""
        parts.append(
            f"<div class='msg {role}'><div class='who'>{who}{ts_html}</div>"
            f"<pre>{html.escape(body)}</pre></div>"
        )
    parts.append("</body></html>")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session", nargs="?",
                         help="path to a session .jsonl file (default: most recently modified across all projects)")
    parser.add_argument("--out", help="output file path (default: alongside the session file)")
    parser.add_argument("--format", choices=["md", "html"], default="md")
    parser.add_argument("--full", action="store_true",
                         help="include complete tool inputs/outputs instead of a one-line summary")
    parser.add_argument("--include-thinking", action="store_true",
                         help="include extended-thinking text if present")
    parser.add_argument("--max-chars", type=int, default=1200,
                         help="truncate each tool result preview to this many characters (default 1200); "
                              "ignored with --full")
    args = parser.parse_args()

    session_path = Path(args.session) if args.session else find_latest_session()
    if not session_path.exists():
        raise SystemExit(f"Not found: {session_path}")

    entries = convert(session_path, args.include_thinking, args.full, args.max_chars)

    ext = ".html" if args.format == "html" else ".md"
    out_path = Path(args.out) if args.out else session_path.with_suffix(ext)

    if args.format == "html":
        write_html(entries, out_path, session_path)
    else:
        write_markdown(entries, out_path, session_path)

    print(f"Wrote {len(entries)} messages to {out_path}")


if __name__ == "__main__":
    sys.exit(main())
