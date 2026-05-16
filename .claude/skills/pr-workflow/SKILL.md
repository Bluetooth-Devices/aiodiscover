---
name: pr-workflow
description: Create pull requests for bluetooth-devices/aiodiscover. Use when creating PRs, submitting changes, or preparing contributions.
allowed-tools: Read, Bash, Glob, Grep
---

# aiodiscover PR Workflow

When creating a pull request for `bluetooth-devices/aiodiscover`,
follow these steps. Repo-wide conventions live in
[CLAUDE.md](../../../CLAUDE.md); this skill summarises the parts
that matter at PR-creation time.

## 1. Create branch from origin/main

`origin` points at `bluetooth-devices/aiodiscover`; there is no
fork in this workflow. Always re-fetch first so the branch is
based on the latest `main`:

```bash
git fetch origin
git checkout -b <branch-name> origin/main
```

`CONTRIBUTING.md` suggests branch names of the form
`{type}/short-description` (e.g. `feature/read-tiff-files`,
`bugfix/handle-file-not-found`). It's a soft convention — not
enforced by CI — but match it when convenient.

## 2. Conventional Commits — for both commit subjects AND PR title

Every commit subject MUST follow
[Conventional Commits](https://www.conventionalcommits.org/), and
the PR title should too. The CI gate is the per-commit
`commitlint` job in `.github/workflows/ci.yml` (running
`wagoid/commitlint-github-action` against
`@commitlint/config-conventional`).

Unlike some sibling repos, **there is no separate
`pr-title.yml` workflow** — only the per-commit linter runs.
GitHub still uses the PR title as the squash-merge commit
subject, so a malformed title becomes a malformed commit on
`main`, which `python-semantic-release` will then either
miscategorise or skip. Treat the PR title with the same care
as a commit subject.

Accepted types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`,
`perf`, `refactor`, `revert`, `style`, `test`. Scope is optional.
The subject (text after `type(scope):`) must start lowercase.
`commitlint.config.mjs` explicitly disables header / body /
footer length limits, so long subjects won't fail CI — but keep
them readable. Examples that pass:

```
feat: add async context manager to DiscoverHosts
fix(network): fall back to arp -an on macOS when pyroute2 is missing
perf!: drop python 3.10 support
```

### Pick the type that matches the release impact

`python-semantic-release` reads the commit log on `main` to
decide the next version, write `CHANGELOG.md`, bump
`pyproject.toml:project.version` and
`aiodiscover/__init__.py:__version__`, tag, push, and publish to
PyPI:

| Type                                                               | Release effect                                  |
| ------------------------------------------------------------------ | ----------------------------------------------- |
| `feat:`                                                            | minor bump, shows in changelog under Features   |
| `fix:`, `perf:`                                                    | patch bump, shows under Bug Fixes / Performance |
| any with `!` or `BREAKING CHANGE:` footer                          | major bump                                      |
| `chore:`, `docs:`, `test:`, `ci:`, `style:`, `build:`, `refactor:` | no bump                                         |

`chore*` and `ci*` are also stripped from the changelog
entirely (see `exclude_commit_patterns` in
`[tool.semantic_release.changelog]` in `pyproject.toml`). A
user-visible bugfix tagged `chore:` will be silently omitted
from the changelog; an internal refactor tagged `feat!:` will
mint a fake major release. Pick the type a changelog reader
would expect.

## 3. Fill in the PR template

Unlike `dbus-fast`, this repo **does** ship
`.github/PULL_REQUEST_TEMPLATE.md`. It has two sections:

- **Description of change** — what the change does, why, and
  how you verified it. Use `Fixes #N` (or `closes #N`) here so
  GitHub auto-closes the issue on merge. If the change is
  related to an issue but doesn't close it, just reference the
  number without the keyword.
- **Pull-Request Checklist** — six tickboxes (up-to-date with
  `main`, follows contributing guidelines, links issues with
  `Fixes #N`, has new/updated unit tests, docs updated,
  Conventional Commits). The template's own comment notes that
  unchecked boxes are fine when the PR opens, and `N/A` is an
  acceptable answer for any item that doesn't apply (e.g. a
  pure docs change doesn't need new unit tests).

Don't delete the template structure — fill it in. Reviewers
expect to see those headings.

## 4. Commit hygiene

- **Imperative-mood subject line** — "add X", not "added X".
  Lowercase first word (commitlint rule).
- **No `Co-Authored-By` trailers for LLM tools.** Project
  preference — commits attribute the human who reviewed the
  change.
- **Don't hand-edit `__version__` or `project.version`.**
  `python-semantic-release` owns both and will overwrite manual
  edits on the next release.
- Let pre-commit run (commitizen on `commit-msg`, plus ruff
  lint + format, prettier, codespell, mypy, and the standard
  `pre-commit-hooks` set on `pre-commit`). If a hook auto-fixes
  a file, the commit aborts — re-stage the auto-fixed files and
  re-commit. The full hook list is in `.pre-commit-config.yaml`.
- Write tests for behavioural changes — they live **inside**
  the package at `aiodiscover/tests/` (not at a top-level
  `tests/` directory). Tests are mocked end-to-end; no network
  or system services are required:

  ```bash
  poetry run pytest
  ```

  `addopts` in `pyproject.toml` already enables verbose mode
  and coverage against `aiodiscover` — no extra flags needed.

## 5. Push and create the PR

```bash
git push -u origin <branch-name>
gh pr create --repo bluetooth-devices/aiodiscover --base main \
  --title "type(scope): lowercase subject" \
  --body-file /tmp/pr-body.md
```

Use `--body-file` rather than `--body "..."` so that backticks,
asterisks, and other Markdown in the body are passed through
verbatim instead of being mangled by shell quoting.

If the title fails the per-commit `commitlint` check on the
squash-merge commit (which would only surface when a maintainer
hits "Squash and merge"), you can fix it in the GitHub UI or
with `gh pr edit --title "..."`; no push is needed.

## 6. After the PR is open

The CI workflow `.github/workflows/ci.yml` runs four jobs:

- **`lint`** — pre-commit on Python 3.x (ruff lint + format,
  prettier, codespell, mypy, and the standard hooks).
- **`commitlint`** — per-commit Conventional Commits check
  via `wagoid/commitlint-github-action@v6.2.1`, with
  `fetch-depth: 0` so it can walk the branch.
- **`test`** matrix — Python 3.10, 3.11, 3.12, 3.13, 3.14 ×
  ubuntu-latest, macOS-latest, windows-latest (15 cells, all
  required green). Coverage is uploaded to Codecov.
- **`release`** — needs all three above. On PRs from non-main
  branches it does a `python-semantic-release` dry run
  (`no_operation_mode: true`); on `main` after merge it does
  the real release: bumps versions, writes `CHANGELOG.md`, tags,
  pushes via the SSH deploy key, and uploads to PyPI as well as
  GitHub Releases.

A red `lint` job is usually the pre-commit auto-fix you forgot
to re-stage. A red cell only on Windows or macOS in the `test`
matrix usually means a Linux-only path leaked into a code path
that should be platform-agnostic — check `aiodiscover/network.py`
for the `sys.platform` / try-import pattern. A red cell only on
Python 3.10 usually means a 3.11+ stdlib import slipped in
without going through the `aiodiscover/util.py` shim.

Dependabot PRs are auto-merged by `.github/workflows/auto-merge.yml`
once CI is green; human PRs are not. Plan on a manual review,
followed by a "Squash and merge" step.
