#!/usr/bin/env python3
"""Claude Code status line.

`render` (stdin: status line JSON) -> 2 lines:
  model | context used/window | session lines changed | branch or worktree (via `wt`) | PR
  5h limit | 7d limit | prompt cache [| session cost | session duration]
`render --subagent` (stdin: {"tasks": [...]}) -> one JSON line per task.
Bars are Material Design circles by default; `--bar circles-fa` for Font Awesome circles, `--bar line` for ▬.
`--show cost,duration,description` turns on the optional segments; `--icons emoji` for subagent status.

`setup` points statusLine and subagentStatusLine in settings.json at the `cc-statusline` command.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import unicodedata
from typing import Any, NoReturn

# Nerd Font glyphs
ROBOT = "\U000f06a9"  # nf-md-robot
CHIP = ""  # nf-fa-microchip
DIFF = ""  # nf-oct-diff
BRANCH = ""  # nf-pl-branch
TREE = "\U000f0645"  # nf-md-file_tree
CLOCK = "\U000f0150"  # nf-md-clock_outline
CALENDAR = "\U000f00ed"  # nf-md-calendar
HOURGLASS = "\U000f051f"  # nf-md-timer_sand
TIMER = "\U000f051b"  # nf-md-timer_outline
PR = "\uf407"  # nf-oct-git_pull_request
# (full, half, empty), each from one icon set: Unicode ◐ is missing from many Nerd Fonts and
# falls back to another font with a different baseline
CIRCLES = {
    "circles": ("\U000f0765", "\U000f1396", "\U000f0766"),  # nf-md-circle, _half_full, _outline
    "circles-fa": ("\uf111", "\uf042", "\uf10c"),  # nf-fa-circle, _half_stroke, _o
}

RESET = "\033[0m"
DIM = "\033[2m"
GREEN, YELLOW, RED = "\033[32m", "\033[33m", "\033[31m"
CYAN, MAGENTA, BLUE = "\033[36m", "\033[35m", "\033[34m"
SEP = f" {DIM}│{RESET} "
ANSI = re.compile(r"\033\[[0-9;]*m")
BARS = (*CIRCLES, "line")
BAR_STYLE = "circles"  # set from --bar in main()
OPTIONAL = ("cost", "duration", "description")
SHOW: set[str] = set()  # set from --show in main()
ICONS = "nerd"  # set from --icons in main()

WT_CACHE_TTL = 5  # seconds

Json = dict[str, Any]  # a JSON object as json.load returns it

# review_state -> (color, mark)
PR_STATES = {
    "approved": (GREEN, " ✓"),
    "changes_requested": (RED, " ✗"),
    "pending": (YELLOW, " ●"),
    "draft": (DIM, " ○"),
}
# task status -> (color, nerd icon, emoji); unknown statuses get no icon
STATUS = {
    "running": (CYAN, "\uf04b", "🔄"),  # nf-fa-play
    "completed": (GREEN, "\uf00c", "✅"),  # nf-fa-check
    "failed": (RED, "\uf00d", "❌"),  # nf-fa-xmark
    "killed": (DIM, "\uf04d", "⛔"),  # nf-fa-stop
}


def c(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def level(pct: float) -> str:
    return GREEN if pct < 50 else YELLOW if pct < 80 else RED


def human(n: float) -> str:
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div * 0.9995:  # 999_500 would otherwise round to "1000k"
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix if v < 10 else f"{v:.0f}{suffix}"
    return str(int(n))


def duration(seconds: float) -> str:
    s = max(0, int(seconds))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    if d:
        return f"{d}d{h}h"
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def bar(pct: float, style: str | None = None) -> str:
    """circles: 5 cells in half steps (25% -> full, half, 3x empty);
    line: 10 centered cells, empty ones dimmed."""
    # 10 steps either way; half-up, round() is banker's
    steps = int(max(0, min(100, pct)) / 10 + 0.5)
    style = style or BAR_STYLE
    if style == "line":
        # 22m ends dim, keeps caller's color
        return "▬" * steps + DIM + "▬" * (10 - steps) + "\033[22m"
    full, half = divmod(steps, 2)
    full_c, half_c, empty_c = CIRCLES[style]
    return full_c * full + half_c * half + empty_c * (5 - full - half)


def pretty_model(model_id: str | None) -> str:
    """claude-haiku-5-5 -> Haiku 5.5; unknown shapes are returned as-is."""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d))?(?:\D|$)", model_id or "")
    if not m:
        return model_id or ""
    name, major, minor = m.groups()
    return f"{name.capitalize()} {major}" + (f".{minor}" if minor else "")


def context_seg(used: int, window: int | None) -> str:
    if not window:
        return ""
    pct = used * 100 / window
    return c(level(pct), f"{CHIP} {bar(pct)} {human(used)}/{human(window)} ({pct:.0f}%)")


def changes_seg(cost: Json) -> str:
    added, removed = cost.get("total_lines_added") or 0, cost.get("total_lines_removed") or 0
    if not (added or removed):
        return ""
    return f"{DIFF} {c(GREEN, f'+{added}')} {c(RED, f'-{removed}')}"


def pr_seg(pr: Json | None) -> str:
    if not pr or not pr.get("number"):
        return ""
    color, mark = PR_STATES.get(pr.get("review_state") or "", (BLUE, ""))
    return c(color, f"{PR} #{pr['number']}{mark}")


def cost_seg(cost: Json) -> str:
    usd = cost.get("total_cost_usd")
    return f"${usd:.2f}" if usd is not None else ""


def session_seg(cost: Json) -> str:
    ms = cost.get("total_duration_ms") or 0
    return f"{TIMER} {duration(ms / 1000)}" if ms >= 60_000 else ""  # duration() has no seconds


def cells(text: str) -> int:
    """Terminal width of text, ANSI codes dropped; wide (emoji, CJK) characters take 2 cells."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in ANSI.sub("", text))


def trim(text: str, width: int) -> str:
    if cells(text) <= width:
        return text
    out, used = "", 1  # 1 for the …
    for ch in text:
        used += cells(ch)
        if used > width:
            break
        out += ch
    return out + "…"


def wt_items(cwd: str) -> list[Json]:
    """`wt list` items for cwd's repo, cached on disk for WT_CACHE_TTL seconds."""
    # per-user cache dir: a predictable name in a shared /tmp could be pre-planted as a symlink
    folder = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "cc-statusline")
    path = os.path.join(folder, f"wt-{hashlib.sha1(cwd.encode()).hexdigest()[:16]}.json")
    try:
        if time.time() - os.path.getmtime(path) < WT_CACHE_TTL:
            with open(path) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    items = []
    wt = shutil.which("wt")
    if wt:
        try:
            out = subprocess.run(
                [wt, "-C", cwd, "list", "--format=json"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0:
                items = json.loads(out.stdout).get("items", [])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            json.dump(items, f)
    except OSError:
        pass
    return items


def git_seg(items: list[Json]) -> str:
    cur = next((i for i in items if (i.get("worktree") or {}).get("current")), None)
    if not cur:
        return ""
    wt = cur["worktree"]
    branch = cur.get("branch") or (cur.get("head") or {}).get("short_sha", "")
    if wt.get("main"):
        seg = c(MAGENTA, f"{BRANCH} {branch}")
    else:
        name = os.path.basename(wt.get("path", "").rstrip("/"))
        seg = c(BLUE, f"{TREE} {name}") + c(MAGENTA, f" ({BRANCH} {branch})")
    if any(v is True for v in (wt.get("changes") or {}).values()):
        seg += c(YELLOW, " ●")
    up = cur.get("upstream") or {}
    if up.get("ahead"):
        seg += c(GREEN, f" ⇡{up['ahead']}")
    if up.get("behind"):
        seg += c(RED, f" ⇣{up['behind']}")
    if cur.get("marker"):
        seg += f" {cur['marker']}"
    return seg


def others_seg(items: list[Json]) -> str:
    """Markers of the repo's other worktrees, e.g. `󰙅 2🤖 1💬`."""
    counts: dict[str, int] = {}
    for i in items:
        if i.get("marker") and not (i.get("worktree") or {}).get("current"):
            counts[i["marker"]] = counts.get(i["marker"], 0) + 1
    if not counts:
        return ""
    return c(BLUE, TREE) + " " + " ".join(f"{n}{m}" for m, n in counts.items())


def cache_seg(pc: Json | None, now: float) -> str:
    """Prompt cache: time until it goes cold, or `cold`."""
    if not pc or not pc.get("caching_observed"):
        return ""
    left = (pc.get("expires_at") or 0) - now
    if not pc.get("warm") or left <= 0:
        return c(DIM, f"{HOURGLASS} cold")
    return c(GREEN if left >= 300 else YELLOW, f"{HOURGLASS} {duration(left)}")


def with_effort(text: str, effort: str | float | None) -> str:
    # subagent effort may be a numeric token budget instead of a level
    effort = human(effort) if isinstance(effort, (int, float)) else effort
    return text + c(DIM, f" · {effort}") if effort else text


def limit_seg(icon: str, label: str, window: Json | None, now: float) -> str:
    if not window or window.get("used_percentage") is None:
        return ""
    pct = window["used_percentage"]
    seg = f"{icon} {label} " + c(level(pct), f"{bar(pct)} {pct:.0f}%")
    if window.get("resets_at"):
        seg += c(DIM, f" ↻ {duration(window['resets_at'] - now)}")
    return seg


def render_main(data: Json, now: float | None = None, items: list[Json] | None = None) -> str:
    now = time.time() if now is None else now
    model = (data.get("model") or {}).get("display_name") or pretty_model((data.get("model") or {}).get("id"))
    ctx = data.get("context_window") or {}
    usage = ctx.get("current_usage") or {}
    used = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    cost = data.get("cost") or {}
    if items is None:
        cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or os.getcwd()
        items = wt_items(cwd)
    line1 = [
        with_effort(c(CYAN, f"{ROBOT} {model}"), (data.get("effort") or {}).get("level")) if model else "",
        context_seg(used, ctx.get("context_window_size")),
        changes_seg(cost),
        git_seg(items),
        pr_seg(data.get("pr")),
        others_seg(items),
    ]
    limits = data.get("rate_limits") or {}
    line2 = [
        limit_seg(CLOCK, "5h", limits.get("five_hour"), now),
        limit_seg(CALENDAR, "7d", limits.get("seven_day"), now),
        cache_seg(data.get("prompt_cache"), now),
        cost_seg(cost) if "cost" in SHOW else "",
        session_seg(cost) if "duration" in SHOW else "",
    ]
    return "\n".join(SEP.join(s for s in line if s) for line in (line1, line2) if any(line))


def name_seg(t: Json) -> str:
    color, nerd, emoji = STATUS.get(t.get("status") or "", ("", "", ""))
    seg = c(color, (emoji if ICONS == "emoji" else nerd) + " ") if color else ""
    seg += c(CYAN, f"{ROBOT} {t.get('name') or t.get('agentType') or 'agent'}")
    # label falls back to a copy of description; show it only as a real progress summary
    if t.get("label") and t["label"] != t.get("description"):
        seg += c(DIM, f" · {trim(t['label'], 24)}")
    return seg


def render_subagents(data: Json) -> str:
    rows = []
    for t in data.get("tasks") or []:
        segs = [
            name_seg(t),
            with_effort(pretty_model(t.get("model")), t.get("effort")) if t.get("model") else "",
            context_seg(t.get("tokenCount") or 0, t.get("contextWindowSize")),
        ]
        segs = [s for s in segs if s]
        if "description" in SHOW and t.get("description"):
            # the description gets whatever the other segments leave of the row
            room = (data.get("columns") or 100) - cells(SEP.join(segs)) - cells(SEP)
            if room >= 10:
                segs.insert(1, c(DIM, trim(t["description"], room)))
        rows.append(json.dumps({"id": t["id"], "content": SEP.join(segs)}))
    return "\n".join(rows)


def setup_entries(bar_style: str, refresh: int, show: list[str] | None = None, icons: str = "nerd") -> Json:
    # bare name, found on PATH: uv's ~/.local/bin and mise's shims keep it stable across upgrades
    flag = f" --bar {bar_style}" if bar_style != "circles" else ""
    if show:
        flag += f" --show {','.join(sorted(set(show)))}"
    if icons != "nerd":
        flag += f" --icons {icons}"
    main_entry: Json = {"type": "command", "command": f"cc-statusline render{flag}"}
    if refresh:
        main_entry["refreshInterval"] = refresh
    return {
        "statusLine": main_entry,
        "subagentStatusLine": {"type": "command", "command": f"cc-statusline render --subagent{flag}"},
    }


def is_ours(entry: Json) -> bool:
    """This tool's entry, including older ones that ran statusline.py from a clone or ~/.claude/hooks."""
    command = str(entry.get("command", "")) if isinstance(entry, dict) else ""
    return command.startswith("cc-statusline") or "statusline.py" in command


def load_settings(path: str) -> Json:
    """{} for a missing or empty file; SystemExit(2) for anything that isn't a JSON object."""
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError as e:
        die(f"{path} is not valid JSON ({e}); nothing written")
    if not isinstance(data, dict):
        die(f"{path} is not a JSON object; nothing written")
    return data


def die(msg: str) -> NoReturn:
    print(f"cc-statusline: {msg}", file=sys.stderr)
    sys.exit(2)


def write_json(path: str, data: Json) -> None:
    """Atomic: temp file in the same folder, then rename over `path`, keeping its permissions."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def describe(entry: Json | None) -> str:
    return json.dumps(entry, ensure_ascii=False) if entry is not None else "(unset)"


def cmd_setup(args: argparse.Namespace) -> int:
    path = args.settings or os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude", "settings.json")
    # realpath: write through a symlinked settings.json (dotfiles) instead of replacing the link
    path = os.path.realpath(os.path.expanduser(path))
    settings = load_settings(path)
    updates: Json = {}
    refused: list[str] = []
    for key, new in setup_entries(args.bar, args.refresh_interval, args.show, args.icons).items():
        old = settings.get(key)
        if old == new:
            print(f"{key}: already up to date")
        elif old is None or args.force or is_ours(old):
            updates[key] = new
            print(f"{key}:\n  - {describe(old)}\n  + {describe(new)}")
        else:
            refused.append(key)
            print(f"{key} is set to another command:\n  {describe(old)}", file=sys.stderr)
    if refused:
        print("rerun with -f/--force to replace it; nothing written", file=sys.stderr)
        return 1
    if not updates:
        return 0
    if args.dry_run:
        print(f"dry run: {path} not written")
        return 0
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    write_json(path, {**settings, **updates})
    print(f"wrote {path}" + (f" (backup: {path}.bak)" if os.path.exists(path + ".bak") else ""))
    return 0


def non_negative(value: str) -> int:
    n = int(value)
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {n}")
    return n


def show_list(value: str) -> list[str]:
    names = [n for n in value.split(",") if n]
    bad = [n for n in names if n not in OPTIONAL]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown {', '.join(bad)}; choose from {', '.join(OPTIONAL)}")
    return names


def cmd_render(args: argparse.Namespace) -> int:
    global BAR_STYLE, SHOW, ICONS
    BAR_STYLE, SHOW, ICONS = args.bar, set(args.show), args.icons
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    try:
        print(render_subagents(data) if args.subagent else render_main(data))
    except Exception as e:
        traceback.print_exc()
        # a blank status line hides the bug; a non-JSON subagent line would, so keep Claude Code's default rows
        if not args.subagent:
            first = str(e).split("\n")[0]  # one line, or it breaks the status line layout
            print(c(RED, f"cc-statusline: {type(e).__name__}: {first}"))
    return 0


def version() -> str:
    try:
        return importlib.metadata.version("cc-statusline")
    except importlib.metadata.PackageNotFoundError:  # run as a plain file, not installed
        return "dev"


def add_display_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--bar", choices=BARS, default="circles", help="progress bar style (circles)")
    p.add_argument(
        "--show",
        type=show_list,
        action="extend",
        default=[],
        metavar="LIST",
        help=f"comma-separated optional segments: {', '.join(OPTIONAL)}",
    )
    p.add_argument("--icons", choices=("nerd", "emoji"), default="nerd", help="subagent status icons (nerd)")


def parser() -> argparse.ArgumentParser:
    raw = argparse.RawDescriptionHelpFormatter
    p = argparse.ArgumentParser(prog="cc-statusline", description=__doc__, formatter_class=raw)
    p.add_argument("--version", action="version", version=f"%(prog)s {version()}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    sp = sub.add_parser("render", help="render the status line from Claude Code's JSON on stdin")
    sp.set_defaults(func=cmd_render)
    sp.add_argument("--subagent", action="store_true", help="render subagentStatusLine rows instead of statusLine")
    add_display_args(sp)

    sp = sub.add_parser("setup", help="point statusLine and subagentStatusLine in settings.json at cc-statusline")
    sp.set_defaults(func=cmd_setup)
    add_display_args(sp)
    sp.add_argument(
        "--refresh-interval",
        type=non_negative,
        default=60,
        metavar="N",
        help="statusLine refreshInterval, 0 = none (60)",
    )
    sp.add_argument(
        "--settings",
        metavar="PATH",
        help="settings file ($CLAUDE_CONFIG_DIR/settings.json or ~/.claude/settings.json)",
    )
    sp.add_argument("--dry-run", action="store_true", help="show the changes without writing")
    sp.add_argument("-f", "--force", action="store_true", help="replace a status line set by another tool")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
