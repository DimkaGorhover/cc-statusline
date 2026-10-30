from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import pytest

import statusline as sl
from statusline import (
    BRANCH,
    CALENDAR,
    CHIP,
    CLOCK,
    DIFF,
    DIM,
    HOURGLASS,
    PR,
    ROBOT,
    TIMER,
    TREE,
    bar,
    cache_seg,
    duration,
    human,
    load_settings,
    pretty_model,
    render_main,
    render_subagents,
    setup_entries,
    write_json,
)

F, H, E = sl.CIRCLES["circles"]  # expectations assume the default circles bar


def strip(s: str) -> str:
    return sl.ANSI.sub("", s)


@pytest.mark.parametrize(
    ("n", "want"),
    [(54677, "55k"), (1_000_000, "1M"), (1500, "1.5k"), (999, "999"), (999_499, "999k"), (999_500, "1M")],
)
def test_human(n: int, want: str) -> None:
    assert human(n) == want


@pytest.mark.parametrize(
    ("pct", "style", "want"),
    [
        (0, None, E * 5),
        (10, None, H + E * 4),
        (60, None, F * 3 + E * 2),
        (100, None, F * 5),
        (10, "circles-fa", "" + "" * 4),
    ],
)
def test_bar_circles(pct: float, style: str | None, want: str) -> None:
    assert bar(pct, style) == want


def test_bar_line() -> None:
    assert strip(bar(30, "line")) == "▬" * 10
    assert bar(30, "line").startswith("▬▬▬" + DIM)


@pytest.mark.parametrize(("seconds", "want"), [(2 * 3600 + 14 * 60, "2h14m"), (3 * 86400 + 4 * 3600, "3d4h")])
def test_duration(seconds: int, want: str) -> None:
    assert duration(seconds) == want


@pytest.mark.parametrize(
    ("model_id", "want"),
    [
        ("claude-haiku-5-5", "Haiku 5.5"),
        ("claude-opus-4-20250514", "Opus 4"),
        ("claude-sonnet-4-5-20250929", "Sonnet 4.5"),
        ("", ""),
    ],
)
def test_pretty_model(model_id: str, want: str) -> None:
    assert pretty_model(model_id) == want


def test_render_main_linked_worktree() -> None:
    data = {
        "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
        "cost": {"total_lines_added": 12, "total_lines_removed": 3},
        "context_window": {
            "context_window_size": 1_000_000,
            "current_usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 1252,
                "cache_read_input_tokens": 53423,
            },
        },
        "rate_limits": {
            "five_hour": {"used_percentage": 23, "resets_at": 1000 + 8040},
            "seven_day": {"used_percentage": 25, "resets_at": 1000 + 3 * 86400 + 4 * 3600},
        },
        "effort": {"level": "medium"},
        "prompt_cache": {"caching_observed": True, "warm": True, "expires_at": 1000 + 42 * 60},
    }
    linked = [
        {
            "branch": "feat-x",
            "worktree": {
                "path": "/r/repo.feat-x",
                "main": False,
                "current": True,
                "changes": {"staged": False, "modified": True, "diff": {"added": 1}},
            },
            "upstream": {"ahead": 2, "behind": 1},
            "marker": "🤖",
        },
        {"branch": "main", "marker": "💬", "worktree": {"path": "/r/repo", "main": True}},
        {"branch": "b", "marker": "🤖", "worktree": {"path": "/r/repo.b"}},
        {"branch": "c", "marker": "🤖", "worktree": {"path": "/r/repo.c"}},
    ]
    assert strip(render_main(data, now=1000, items=linked)).split("\n") == [
        f"{ROBOT} Opus 5.5 · medium │ {CHIP} {H}{E * 4} 55k/1M (5%) │ {DIFF} +12 -3 │ "
        f"{TREE} repo.feat-x ({BRANCH} feat-x) ● ⇡2 ⇣1 🤖 │ {TREE} 1💬 2🤖",
        f"{CLOCK} 5h {F}{E * 4} 23% ↻ 2h14m │ {CALENDAR} 7d {F}{H}{E * 3} 25% ↻ 3d4h │ {HOURGLASS} 42m",
    ]


def test_render_main_clean_main_branch() -> None:
    main = [
        {
            "branch": "main",
            "worktree": {"path": "/r/repo", "main": True, "current": True, "changes": {"modified": False}},
            "upstream": {"ahead": 0, "behind": 0},
        }
    ]
    assert strip(render_main({"model": {"display_name": "Opus"}}, items=main)) == f"{ROBOT} Opus │ {BRANCH} main"


def test_cache_seg_expired() -> None:
    pc = {"caching_observed": True, "warm": True, "expires_at": 900}
    assert strip(cache_seg(pc, 1000)) == f"{HOURGLASS} cold"


def test_render_subagents() -> None:
    task = {
        "id": "t1",
        "agentType": "explore",
        "model": "claude-haiku-5-5",
        "effort": "low",
        "tokenCount": 34000,
        "contextWindowSize": 200000,
    }
    row = json.loads(render_subagents({"tasks": [task]}))
    assert row["id"] == "t1"
    assert strip(row["content"]) == f"{ROBOT} explore │ Haiku 5.5 · low │ {CHIP} {F}{E * 4} 34k/200k (17%)"
    row = json.loads(render_subagents({"tasks": [{**task, "effort": 16000}]}))
    assert strip(row["content"]).startswith(f"{ROBOT} explore │ Haiku 5.5 · 16k │")


@pytest.mark.parametrize(
    ("pr", "want"),
    [
        ({"number": 42, "review_state": "approved"}, f"{PR} #42 ✓"),
        ({"number": 7, "review_state": "changes_requested"}, f"{PR} #7 ✗"),
        ({"number": 7}, f"{PR} #7"),
    ],
)
def test_render_main_pr(pr: dict, want: str) -> None:
    assert strip(render_main({"pr": pr}, items=[])) == want


def test_render_main_cost_and_duration_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    data = {"cost": {"total_cost_usd": 1.234, "total_duration_ms": 3_720_000}}
    assert render_main(data, items=[]) == ""  # off by default
    monkeypatch.setattr(sl, "SHOW", {"cost", "duration"})
    assert strip(render_main(data, items=[])) == f"$1.23 │ {TIMER} 1h02m"
    assert strip(render_main({"cost": {"total_duration_ms": 59_000}}, items=[])) == ""  # under a minute


def test_render_subagents_status_label_description(monkeypatch: pytest.MonkeyPatch) -> None:
    task = {"id": "t1", "name": "rev", "status": "running", "description": "review auth", "label": "review auth"}
    content = lambda t: strip(json.loads(render_subagents({"tasks": [t]}))["content"])  # noqa: E731
    assert content(task) == f"\uf04b {ROBOT} rev"  # label equal to description is a copy, not progress
    assert content({**task, "status": "?", "label": "reading a-very-long-file-name.py"}) == (
        f"{ROBOT} rev · reading a-very-long-fil…"
    )
    monkeypatch.setattr(sl, "ICONS", "emoji")
    monkeypatch.setattr(sl, "SHOW", {"description"})
    assert content({**task, "status": "completed"}) == f"✅ {ROBOT} rev │ review auth"


def test_subagent_description_fits_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sl, "SHOW", {"description"})
    task = {
        "id": "t1",
        "name": "rev",
        "label": "checking auth.py",
        "description": "x" * 200,
        "model": "claude-haiku-5-5",
        "effort": "high",
        "tokenCount": 34000,
        "contextWindowSize": 200000,
    }
    row = lambda columns: json.loads(render_subagents({"columns": columns, "tasks": [task]}))["content"]  # noqa: E731
    assert sl.cells(row(120)) == 120
    assert "xxx" in row(120)
    assert "xxx" not in row(60)  # the other segments already take more than 60 cells


def test_trim_counts_wide_chars() -> None:
    assert sl.trim("ab", 2) == "ab"
    assert sl.trim("🤖🤖🤖🤖", 7) == "🤖🤖🤖…"  # each 🤖 is 2 cells: 3 + … fit in 7


def test_render_error_is_shown(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"model": {"display_name": 5}}'))

    def boom(_: object) -> NoReturn:
        raise ValueError("first\nsecond")

    monkeypatch.setattr(sl, "render_main", boom)
    assert run("render") == 0
    out = capsys.readouterr()
    assert strip(out.out) == "cc-statusline: ValueError: first\n"
    assert "Traceback" in out.err


def test_render_rejects_unknown_show() -> None:
    assert run("render", "--show", "cost,nope") == 2


def test_wt_items_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(sl.shutil, "which", lambda _: "/bin/wt")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"items": [{"branch": "main"}]}')

    monkeypatch.setattr(sl.subprocess, "run", fake_run)
    assert sl.wt_items("/r/repo") == [{"branch": "main"}]
    assert sl.wt_items("/r/repo") == [{"branch": "main"}]  # fresh cache: wt not run again
    assert len(calls) == 1
    (cache,) = (tmp_path / "cc-statusline").iterdir()
    os.utime(cache, (0, 0))  # expired
    assert sl.wt_items("/r/repo") == [{"branch": "main"}]
    assert len(calls) == 2
    cache.write_text("{broken")
    os.utime(cache, (0, 0))

    def hung(*_: object, **__: object) -> NoReturn:
        raise subprocess.TimeoutExpired("wt", 2)

    monkeypatch.setattr(sl.subprocess, "run", hung)
    assert sl.wt_items("/r/repo") == []  # broken cache and a hung wt: no git info, no crash


def test_render_subagent_flag(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"tasks": [{"id": "t1", "name": "rev"}]}'))
    assert run("render", "--subagent") == 0
    assert json.loads(capsys.readouterr().out)["id"] == "t1"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run("--version") == 0
    assert capsys.readouterr().out.startswith("cc-statusline ")


def run(*argv: str) -> int | str | None:
    try:
        return sl.main(list(argv))
    except SystemExit as e:
        return e.code


@pytest.fixture
def settings(tmp_path: Path) -> str:
    return str(tmp_path / "settings.json")


def setup(settings: str, *args: str) -> int | str | None:
    return run("setup", "--settings", settings, *args)


def test_setup_updates_own_old_entry(settings: str) -> None:
    old = {"type": "command", "command": 'python3 -X lazy_imports=all "/x/cc-statusline/statusline.py" render'}
    write_json(settings, {"model": "opus", "statusLine": old})
    assert setup(settings, "--dry-run") == 0
    assert load_settings(settings)["statusLine"] == old
    assert setup(settings) == 0
    assert load_settings(settings) == {"model": "opus", **setup_entries("circles", 60)}
    assert load_settings(settings + ".bak")["statusLine"] == old
    assert setup(settings) == 0  # noop
    assert not Path(settings + ".tmp").exists()


def test_setup_line_bar_without_refresh(settings: str) -> None:
    assert setup(settings, "--bar", "line", "--refresh-interval", "0") == 0
    s = load_settings(settings)
    assert "refreshInterval" not in s["statusLine"]
    assert "--bar line" in s["subagentStatusLine"]["command"]


def test_setup_writes_cc_statusline_commands(settings: str) -> None:
    assert setup(settings, "--bar", "line") == 0
    s = load_settings(settings)
    assert s["statusLine"]["command"] == "cc-statusline render --bar line"
    assert s["subagentStatusLine"]["command"] == "cc-statusline render --subagent --bar line"


def test_setup_writes_show_and_icons(settings: str) -> None:
    assert setup(settings, "--show", "duration,cost", "--show", "cost", "--icons", "emoji") == 0
    s = load_settings(settings)
    assert s["statusLine"]["command"] == "cc-statusline render --show cost,duration --icons emoji"
    assert s["subagentStatusLine"]["command"] == "cc-statusline render --subagent --show cost,duration --icons emoji"


def test_setup_refuses_foreign_entry_without_force(settings: str) -> None:
    foreign = {"type": "command", "command": "npx ccstatusline@latest"}
    write_json(settings, {"statusLine": foreign})
    assert setup(settings) == 1
    assert load_settings(settings) == {"statusLine": foreign}  # nothing written
    assert setup(settings, "-f") == 0
    assert load_settings(settings)["statusLine"] == setup_entries("circles", 60)["statusLine"]


def test_setup_writes_through_symlink(tmp_path: Path) -> None:
    real = tmp_path / "dotfiles" / "settings.json"
    write_json(str(real), {"model": "opus"})
    link = tmp_path / "settings.json"
    link.symlink_to(real)
    assert setup(str(link)) == 0
    assert link.is_symlink()
    assert load_settings(str(real))["statusLine"] == setup_entries("circles", 60)["statusLine"]


def test_setup_rejects_negative_refresh_interval(settings: str) -> None:
    assert setup(settings, "--refresh-interval", "-5") == 2
    assert not Path(settings).exists()


def test_setup_rejects_invalid_json(settings: str) -> None:
    Path(settings).write_text("{oops")
    assert setup(settings) == 2


def test_setup_creates_missing_file(tmp_path: Path) -> None:
    assert setup(str(tmp_path / "new" / "settings.json")) == 0
