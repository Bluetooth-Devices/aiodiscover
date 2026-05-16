# Notes for LLM contributors

A short orientation file for an LLM working in this repo. Skim
before making changes; keep edits consistent with what's described
here. Read [README.md](README.md) for the user-facing intro and
[CONTRIBUTING.md](CONTRIBUTING.md) for the human contributor flow
(but note that some of `CONTRIBUTING.md` is stale — see
_Documentation drift_ below).

## What this project is

`aiodiscover` is a small, async Python library that enumerates
hosts on the local network by combining two sources:

- **ARP table walks** — populated by sending probe traffic to
  every address in the local subnet, then reading the system ARP
  cache via `pyroute2` on Linux or `arp -an` on macOS / Windows.
- **DNS PTR lookups** — the discovered IPs are reverse-resolved
  via `aiodns` against the system resolver to recover hostnames.

The public surface is a single class — `DiscoverHosts` (in
`aiodiscover/discovery.py`) — exposing one coroutine,
`async_discover()`, that returns a list of
`{hostname, ip, macaddress}` dicts.

The most important downstream user is
[Home Assistant](https://www.home-assistant.io/), which calls
`async_discover()` from its `dhcp` integration to find devices on
the user's LAN. That makes a few things load-bearing:

- The library runs on **Linux, macOS, and Windows** — the CI
  matrix covers all three. Anything Linux-specific (notably
  `pyroute2`) lives behind `sys.platform` / try-import guards in
  `aiodiscover/network.py`.
- It runs against **whatever DNS resolver the user has
  configured**, including public resolvers like Quad9 / 1.1.1.1.
  Excessive or malformed PTR queries can get end users
  rate-limited or banned upstream — be conservative with query
  volume and resolver behaviour. (See `fix_ptr_recursion.py` at
  the repo root for an in-progress investigation into adding
  `ARES_FLAG_NORECURSE` to suppress recursion on PTR lookups.)
- It must not import or fail on missing optional Linux-only
  dependencies on macOS / Windows.

## Code style

- **Docstrings: terse, default to single-line.** A docstring is
  the function's _contract_, not its narrative. Almost every
  docstring should be one line — `"""Summary."""` — describing
  what the function does. Multi-line is the exception, only
  justified when there is non-obvious caller-visible behaviour
  the type signature and parameter names don't already convey.

  **What does NOT belong in docstrings or comments:**
  - Rationale / motivation / "why we used to do X" — that's the
    PR description and the commit message. Git already remembers.
  - Cross-references to issue numbers ("closes #N", "follow-up
    to #M") — the PR body carries those.
  - Restatement of the function body in prose. If the next line
    of the docstring is just describing what the next line of
    code does, delete the docstring line.
  - Test docstrings retelling the production-side story. A test
    docstring should name what the test pins, in one sentence —
    not re-explain the bug, the fix, or the surrounding flow.

- **Comments**: same bar. Default to writing no comments. Add
  one only when the _why_ is non-obvious: a hidden constraint, a
  subtle invariant, a workaround for a specific bug, behaviour
  that would surprise a reader. If removing the comment wouldn't
  confuse a future reader, don't write it.

  **Don't remove existing comments** unless the code they
  describe is gone — the original author left them for a reason.
  In particular, the `sys.platform` and `try`/`except ImportError`
  branches in `network.py` exist _because_ removing them broke
  the macOS / Windows test legs.

- **Method order**: public API at the top, private helpers
  (`_underscore_prefixed`) at the bottom.

- **Line length**: 88 (ruff default; configured in
  `pyproject.toml`). `requires-python = ">=3.10"`,
  `target-version = "py310"` for ruff and the implicit floor for
  `pyupgrade` (ruff's `UP` rules). Don't introduce 3.11+-only
  syntax — for example, `aiodiscover/util.py` exists solely to
  paper over `asyncio.timeout` not being available on 3.10. The
  pattern is:

  ```python
  if sys.version_info[:2] < (3, 11):
      from async_timeout import timeout as asyncio_timeout
  else:
      from asyncio import timeout as asyncio_timeout
  ```

  If you need a stdlib feature added in 3.11 or later, route it
  through a similar shim rather than dropping 3.10 support.

- **Imports**: ruff-isort sorted, with
  `known-first-party = ["aiodiscover", "tests"]`. Prefer absolute
  imports rooted at `aiodiscover.*`. Use
  `from __future__ import annotations` — every existing module
  under `aiodiscover/` does, which lets the type hints evaluate
  lazily under 3.10.

- **Typing**: mypy runs in strict mode (`disallow_untyped_defs`,
  `disallow_incomplete_defs`, `disallow_any_generics`,
  `warn_unreachable`, `warn_unused_ignores`). New production
  code must be fully typed; tests are exempted via the
  `tests.*` override in `pyproject.toml`.

## Commit / PR conventions

- **Conventional Commits, lowercase subject.** The repo runs
  `@commitlint/config-conventional` on every commit (via the
  `commitlint` CI job in `ci.yml` using
  `wagoid/commitlint-github-action`, plus the `commitizen`
  pre-commit hook on `commit-msg`). Accepted types: `build`,
  `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`,
  `revert`, `style`, `test`. Scopes are optional. The subject
  (text after `type(scope):`) must start lowercase. Header,
  body, and footer length limits are explicitly disabled in
  `commitlint.config.mjs`, but still keep subject lines
  readable. Examples that pass:
  - `feat: add async context manager to DiscoverHosts`
  - `fix(network): fall back to arp -an on macOS when pyroute2 is missing`
  - `perf!: drop python 3.10 support`

- **No separate PR-title gate.** Unlike some sibling repos
  (e.g. `dbus-fast`), this repo does **not** run
  `amannn/action-semantic-pull-request` on the PR title — only
  the per-commit `commitlint` job runs. That said, GitHub uses
  the PR title as the squash-merge subject, so it ends up in
  the commit log on `main` and **must still parse as a valid
  Conventional Commit** for the next `python-semantic-release`
  run to classify it correctly. Treat the PR title with the
  same care as a commit subject.

- **Releases are commit-driven.** `python-semantic-release`
  (configured in `pyproject.toml` under `[tool.semantic_release]`)
  runs from the `release` job in `ci.yml` after `test`, `lint`,
  and `commitlint` pass on `main`. It reads the commit log,
  decides the next version, bumps `pyproject.toml`'s
  `project.version` and `aiodiscover/__init__.py:__version__`,
  writes `CHANGELOG.md`, tags, pushes, uploads to PyPI via
  `pypa/gh-action-pypi-publish`, and attaches artefacts to a
  GitHub release. Bump rules:
  - `feat:` → minor bump.
  - `fix:` / `perf:` → patch bump.
  - Anything with `!` or a `BREAKING CHANGE:` footer → major.
  - `chore:`, `docs:`, `test:`, `ci:`, `style:`, `build:`,
    `refactor:` → no bump.
  - `chore*` and `ci*` are also stripped from the changelog
    entirely (`exclude_commit_patterns` in
    `[tool.semantic_release.changelog]`). A user-visible bugfix
    tagged `chore:` will be silently omitted from the
    changelog — pick the type a changelog reader would expect.

- **No `Co-Authored-By` trailers for LLM authorship.** Project
  preference: commits attribute the human who reviewed the
  change, not the tool that produced the draft.

- **There IS a PR template** at
  `.github/PULL_REQUEST_TEMPLATE.md` — a description block plus
  a checklist (code up-to-date with `main`, follows
  contributing guidelines, links issues with `Fixes #N`, has
  unit tests, docs updated, commits follow Conventional
  Commits). Fill in the description block; the checklist items
  may be ticked or marked `N/A` per the template's own note.

- **Pre-commit auto-fixes; re-stage.** `ruff --fix`,
  `ruff-format`, `prettier`, `codespell`, `mypy`, and the
  standard `pre-commit-hooks` set
  (trailing-whitespace, end-of-file-fixer, debug-statements,
  check-yaml/json/toml/xml, check-builtin-literals,
  check-case-conflict, check-docstring-first,
  detect-private-key) all run on commit and will modify files
  in place. When a hook rewrites a file, the commit aborts —
  re-stage the auto-fixed files and commit again.

## Running tests

The suite is pure-Python and self-contained — no network,
no system services required (the network calls are mocked).
Just:

```bash
poetry install
poetry run pytest
```

`addopts` in `pyproject.toml` already enables verbose mode,
coverage against `aiodiscover`, and term-missing reporting, so
you don't need to pass extra flags for the normal flow. Tests
use `pytest.mark.asyncio` (one mark per test) — see
`aiodiscover/tests/test_discovery.py` for the pattern.

The CI matrix (`.github/workflows/ci.yml`) runs Python
**3.10, 3.11, 3.12, 3.13, 3.14** on **ubuntu-latest,
macOS-latest, windows-latest** — fifteen cells, all required
green. Most regressions you'll see in CI but not locally are
one of:

- A 3.11+ stdlib import that you forgot to gate via
  `aiodiscover/util.py`-style shim.
- A Linux-only code path (`pyroute2`, `/proc`,
  `subprocess` invocation of `ip`/`arp`) that wasn't guarded.
  `aiodiscover/network.py` already does the platform branching;
  follow its pattern.
- A test that assumed a POSIX `subprocess` quoting model and
  fails under Windows. The Windows leg sets the
  `WindowsSelectorEventLoopPolicy` in
  `aiodiscover/tests/test_discovery.py`; if you add new
  network-touching tests, mock at the same boundary the
  existing tests do.

## Build conventions

- **Pure Python, Poetry.** `[build-system]` uses
  `poetry-core>=2.0.0`; there's no Cython, no C extension, no
  `build_ext.py`. `poetry build` produces both an sdist and a
  wheel directly.
- **Single source for the version.** `__version__` in
  `aiodiscover/__init__.py` and `project.version` in
  `pyproject.toml` are kept in sync by
  `python-semantic-release`. Don't edit either by hand —
  semantic-release will overwrite both during the release job.
- **Runtime dependencies are pinned to floors, not ceilings**
  in `pyproject.toml`: `aiodns>=3.1.1`, `ifaddr>0.0.0`,
  `pyroute2>=0.9.6`, `cached_ipaddress>=0.2.0`. The one
  conditional is `async_timeout = { version = ">=4.0.1",
python = "<3.11" }` — needed only for the 3.10 leg, paired
  with the shim in `aiodiscover/util.py`.
- **Test-only dependencies** live in
  `[tool.poetry.group.dev.dependencies]`. Docs deps live in
  `[tool.poetry.group.docs.dependencies]` and are marked
  `optional = true`, gated to Python 3.11+.

## Useful entry points

| Path                                  | What                                                                  |
| ------------------------------------- | --------------------------------------------------------------------- |
| `aiodiscover/__init__.py`             | Package entry point — re-exports `DiscoverHosts`, holds `__version__` |
| `aiodiscover/discovery.py`            | `DiscoverHosts` class + DNS PTR resolution + chunking / batching      |
| `aiodiscover/network.py`              | `SystemNetworkData` — ARP + interface enumeration; platform branches  |
| `aiodiscover/util.py`                 | `asyncio_timeout` compat shim (3.10 → `async_timeout`, 3.11+ stdlib)  |
| `aiodiscover/tests/test_discovery.py` | Tests for `DiscoverHosts` and the DNS PTR pipeline                    |
| `aiodiscover/tests/test_network.py`   | Tests for ARP cache reading and interface enumeration                 |
| `aiodiscover/tests/test_init.py`      | Smoke test for top-level imports                                      |
| `aiodiscover/tests/conftest.py`       | Shared pytest fixtures                                                |
| `pyproject.toml`                      | Poetry config + ruff / mypy / pytest / semantic-release settings      |
| `commitlint.config.mjs`               | Conventional Commits config (header/body/footer length limits off)    |
| `.pre-commit-config.yaml`             | Pre-commit hook set (commitizen, ruff, mypy, prettier, codespell, …)  |
| `.github/workflows/ci.yml`            | Lint + commitlint + matrix tests + semantic-release / PyPI publish    |
| `.github/PULL_REQUEST_TEMPLATE.md`    | PR template — description + checklist                                 |

## Documentation drift

A few committed docs are older than the current toolchain.
Trust `pyproject.toml` and `.github/workflows/ci.yml` over
prose in the following:

- `CONTRIBUTING.md` describes a `pip install -e .[dev]` /
  `make build` (tox) / `bump2version` flow. The actual flow is
  Poetry + `poetry run pytest` + automatic versioning by
  `python-semantic-release`. The high-level intent (fork,
  branch, PR, Conventional Commits) is still correct.
- `tox.ini` lists `py37, py38, py39` and uses `flake8` /
  `black` — none of which match current reality (3.10–3.14, ruff).
  It's effectively dead; `pytest` is invoked directly by CI.
- `Makefile`'s `build` target shells out to `tox`, so it
  inherits the same staleness. `make docs` / `make gen-docs`
  are still useful for the Sphinx docs.
- `README.md`'s "The Four Commands You Need To Know" section
  is cookiecutter boilerplate and references Python 3.7 / 3.8
  and `bump2version`. Don't mirror its phrasing in new docs.

## Things not to do

- **Don't introduce 3.11+-only syntax or stdlib imports** — the
  package supports 3.10+. If you need an asyncio feature added
  in 3.11+, follow the `aiodiscover/util.py` shim pattern.
- **Don't remove the platform guards in `network.py`** —
  `pyroute2` is Linux-only, the `arp -an` fallback is for
  macOS / Windows, and the Windows test leg specifically pins
  `WindowsSelectorEventLoopPolicy`. The CI matrix will fail
  loudly if any of these regress, but the failures aren't
  always intuitive to debug from the Linux dev box.
- **Don't pick a Conventional Commit type that under- or over-
  states the release impact.** `chore:` for a user-visible
  bugfix hides it from the changelog (`chore*` is in
  `exclude_commit_patterns`); `feat!:` for an internal refactor
  mints a fake major release.
- **Don't add `Co-Authored-By` trailers for LLM tools.**
  Project preference — see _Commit / PR conventions_ above.
- **Don't hand-edit `__version__` or `project.version`.**
  `python-semantic-release` owns both; manual edits will be
  overwritten on the next release and may confuse the version
  bumper if they don't match.
- **Don't commit the repo-root scratch files** (`out`,
  `fix_ptr_recursion.py`, `no_recurse_default_pr.md`,
  `test_recursion_flag.py`, `profile_discovery.py`,
  `demo.py`) as part of an unrelated change. They're
  in-progress investigation artefacts living outside the
  package and outside the test suite — leave them where they
  are unless you're explicitly working on the PTR-recursion
  effort.
- **Don't expand the install footprint casually.** This
  library is on the import path of every Home Assistant
  install; new runtime dependencies need to justify their
  weight on import time and wheel size.
