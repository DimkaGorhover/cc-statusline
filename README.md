# cc-statusline

A two-line [Claude Code](https://code.claude.com/docs/en/statusline) status line in one Python file,
with no dependencies.

![cc-statusline in Claude Code](assets/screenshot.png)

## What it shows

Line 1:

- **Model and effort**: `Haiku 5.5 · low`
- **Context**: how much of the context window is used, as a bar, token counts and a percentage
- **Lines changed** this session: `+12 -3`, shown only after the first edit
- **Branch or worktree**, from [`wt`](https://github.com/max-sixty/worktrunk):
  - `●` when there are uncommitted changes
  - `⇡`/`⇣` for commits ahead of or behind upstream
  - the worktree's marker, e.g. 🤖 while Claude works and 💬 while it waits
  - the markers of the repo's other worktrees, e.g. `2🤖 1💬`
- **Pull request** for the branch: `#42`, with ✓ approved, ✗ changes requested, ● pending or ○ draft
  (for a GitLab merge request, Claude Code reports ✓ when it is mergeable, not only when approved)

Line 2:

- **5-hour and 7-day rate limits**: usage bar, percentage and time until reset
- **Prompt cache**: time until it goes cold
- **Session cost and duration**: `$1.23`, `1h02m`, off by default (`--show cost,duration`)

Each segment is hidden when Claude Code doesn't send its data. The colors go green, yellow and
red at 50% and 80%.

Subagents get their own row with status (running, completed, failed, killed), name, progress, model,
effort and context. The task description is off by default (`--show description`).

## Requirements

- Python 3.9+ (macOS's `/usr/bin/python3` works)
- A [Nerd Font](https://www.nerdfonts.com/) in your terminal, for the icons
- Optional: [`wt`](https://github.com/max-sixty/worktrunk) for the branch/worktree segment.
  Without it, that segment is hidden. The `wt list` result is cached for 5 seconds in
  `$XDG_CACHE_HOME/cc-statusline` (`~/.cache/cc-statusline` by default).
- Optional: Worktrunk's Claude Code plugin, which sets the 🤖/💬 markers:
  `wt config plugins claude install`. You can also set a marker yourself with
  `wt config state marker set 🚧`.

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/DimkaGorhover/cc-statusline
cc-statusline setup
```

Or with [mise](https://mise.jdx.dev/):

```sh
mise use -g pipx:git+https://github.com/DimkaGorhover/cc-statusline
cc-statusline setup
```

To pin a release, add the tag to the URL: `git+https://github.com/DimkaGorhover/cc-statusline@v0.2.1`.

`setup` points `statusLine` and `subagentStatusLine` in `~/.claude/settings.json` (or
`$CLAUDE_CONFIG_DIR/settings.json`) at `cc-statusline render` and `cc-statusline render --subagent`.
Claude Code finds the command on your PATH (`~/.local/bin` for uv, mise's shims), so upgrades never
change settings.json. `setup` saves a backup to `settings.json.bak` first. If settings.json is a
symlink, for example into a dotfiles repo, `setup` writes the file it points to.

If another status line is already configured, `setup` refuses and changes nothing. Rerun it with
`-f` to replace that status line. Older entries that ran `statusline.py` directly are updated
without `-f`.

## Upgrade

```sh
uv tool upgrade cc-statusline   # or: mise up cc-statusline
cc-statusline setup
```

Rerunning `setup` picks up any change to the settings.json entries, and prints `already up to date`
when there is none.

## Options

```sh
cc-statusline setup [--bar STYLE] [--show LIST] [--icons SET] [--refresh-interval N] [--settings PATH] [--dry-run] [-f]
cc-statusline render [--subagent] [--bar STYLE] [--show LIST] [--icons SET]   # what Claude Code runs; reads its JSON on stdin
cc-statusline --version
```

| Option                 | Default                    | Description                                                                          |
| ---------------------- | -------------------------- | ------------------------------------------------------------------------------------ |
| `--bar STYLE`          | `circles`                  | Bar style: `circles` (Material Design), `circles-fa` (Font Awesome) or `line` (`▬`) |
| `--show LIST`          |                            | Optional segments, comma-separated: `cost`, `duration`, `description`               |
| `--icons SET`          | `nerd`                     | Subagent status icons: `nerd` (Nerd Font) or `emoji`                                 |
| `--refresh-interval N` | `60`                       | `refreshInterval` for statusLine, in seconds; `0` leaves it unset                    |
| `--settings PATH`      | `~/.claude/settings.json`  | Settings file to write                                                               |
| `--dry-run`            |                            | Show the changes without writing                                                     |
| `-f`, `--force`        |                            | Replace a status line set by another tool                                            |

## Development

```sh
mise install          # prek, ruff, ty, uv, gitleaks
prek install          # git pre-commit hook
mise run test         # tests (pytest needs Python 3.10+)
mise run pre-commit   # all hooks on all files
```

pytest can't run on the 3.9 the script supports, so the `py39-smoke` hook also renders
`tests/input.json` on Python 3.9.

## Inspired by

- [ccstatusline](https://github.com/sirmalloc/ccstatusline) by sirmalloc
- [claude-statusline](https://github.com/ProcessPal/claude-statusline) by ProcessPal

## License

[MIT](LICENSE)
