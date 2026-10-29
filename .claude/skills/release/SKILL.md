---
name: release
description: Use when the user asks to release, cut or publish a version, "bump the version", tag or push a release of cc-statusline - any combination of those words, e.g. "bump, tag and push" or "release a patch".
argument-hint: "[patch|minor|major]"
---

# Releasing

A release is a version bump in `pyproject.toml`, a `vX.Y.Z` tag on `main` and a GitHub Release.
There is no PyPI: users install from the git URL, so the tag is what `@vX.Y.Z` pins. Do only the
steps the user asked for ("bump and commit" means no tag, no push), but never tag without a bump or
push a tag without its commit.

## 1. Check the starting point

```bash
git switch main && git pull --ff-only
git status --short
LAST=$(git tag --sort=-v:refname | head -1); echo "${LAST:-no tags yet}"
git log --oneline ${LAST:+$LAST..}HEAD
```

- Tags go only on `main`. On another branch, stop and ask: merge first, or skip the tag
- Uncommitted changes are not part of a release. Ask: commit them first, or stash
- Nothing new since the last tag → no release. Say so and stop

## 2. Pick the increment

`$ARGUMENTS` wins when it names one. Otherwise read the Conventional Commits since `$LAST`; the
largest applies:

| Commits since the last tag              | Increment                        |
| --------------------------------------- | -------------------------------- |
| only `fix:`, `perf:`                    | `patch`                          |
| any `feat:`                             | `minor`                          |
| any `!` or `BREAKING CHANGE:`           | `major`, but `minor` while 0.x   |
| only `docs:`, `test:`, `build:`, `chore:` | no release; ask if one is wanted |

Say which commits decided it, so the user can override.

## 3. Check the tag is free

```bash
NEW=$(uv version --bump <increment> --dry-run --short)   # e.g. 0.3.0
git tag -l "v$NEW"; git ls-remote --tags origin "v$NEW"
```

If it exists, stop and ask: delete a local-only tag (`git tag -d "v$NEW"`) or take the next version.
Never move a tag that is already on `origin`.

`pyproject.toml` can already be ahead of the last tag (a branch bumped it, e.g. 0.2.0 with no tags
yet). Then release that version as it is: skip the bump in step 4 and set `NEW` from
`uv version --short`.

## 4. Bump

```bash
uv version --bump <increment>          # pyproject.toml and uv.lock together
sed -i '' "s|cc-statusline@v[0-9.]*|cc-statusline@v$NEW|" README.md   # the pin example
```

Use `uv version`, not a hand edit: it keeps `uv.lock` in step. `cc-statusline --version` prints
the pyproject version, so it must match the tag.

## 5. Checks until clean

```bash
mise run pre-commit || mise run pre-commit
```

The first run can fail only because a formatter rewrote files. If the second fails too, fix the
cause. Never use `--no-verify`.

## 6. Write the release notes

Invoke the `devkit:changelog-generator` skill for the range `$LAST..HEAD` (all of history when there
is no tag yet), as GitHub release body for `v$NEW`. Save its output to
`$TMPDIR/cc-statusline-v$NEW.md` (your scratchpad when you have one), not into the repo. Show it to
the user before publishing.

## 7. Commit, tag, push, release

```bash
git add pyproject.toml uv.lock README.md
git commit -q -m "chore(release): v$NEW"
git tag -a "v$NEW" -m "v$NEW"                  # always the v prefix
git push origin main "v$NEW"
gh release create "v$NEW" --title "v$NEW" --notes-file "$NOTES"   # NOTES = the file from step 6
```

The prek `pre-commit` hook runs again on commit. If it rewrites files the commit is aborted:
re-add and commit again. Don't amend.

Pushing and creating the GitHub Release are public. Do them only when the user asked to push or
release, and confirm first if they only said "bump" or "tag".

## 8. Verify and report

```bash
git ls-remote --tags origin "v$NEW"
gh release view "v$NEW" --json url,tagName --jq '.url'
uv tool install --force "git+https://github.com/DimkaGorhover/cc-statusline@v$NEW" && cc-statusline --version
```

Report the version, the commit hash, the release URL and the installed `--version`. Remind the user
that installs from the plain git URL follow `main`, so `uv tool upgrade cc-statusline` picks up the
release; pinned `@v…` installs need a reinstall with the new tag.
