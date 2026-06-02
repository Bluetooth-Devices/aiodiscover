# Async Host discovery

[![CI Status](https://github.com/bluetooth-devices/aiodiscover/actions/workflows/ci.yml/badge.svg)](https://github.com/bluetooth-devices/aiodiscover/actions/workflows/ci.yml)
[![Documentation Status](https://readthedocs.org/projects/aiodiscover/badge/?version=latest)](https://aiodiscover.readthedocs.io/en/latest/?badge=latest)
[![Code Coverage](https://codecov.io/gh/bluetooth-devices/aiodiscover/branch/main/graph/badge.svg)](https://codecov.io/gh/bluetooth-devices/aiodiscover)

Discover hosts by arp and ptr lookup

---

## Features

- Discover hosts on the network via ARP and PTR lookup

## Quick Start

```python
import asyncio
import pprint
from aiodiscover import DiscoverHosts


async def main() -> None:
    async with DiscoverHosts() as discover_hosts:
        pprint.pprint(await discover_hosts.async_discover())


asyncio.run(main())
```

For long-lived consumers, reuse a single `DiscoverHosts` instance across
scans and call `await discover_hosts.close()` at shutdown to release the
underlying DNS resolver and netlink socket.

### Picking a specific interface

On hosts with more than one network interface, pass `local_ip` to pin
discovery to the subnet of a chosen interface instead of letting the
default route decide:

```python
async with DiscoverHosts(local_ip="192.168.5.10") as discover_hosts:
    await discover_hosts.async_discover()
```

`local_ip` is the IPv4 address bound to the interface you want to scan
on. Without it, `aiodiscover` infers the subnet from whichever interface
provides a route to a well-known target. Invalid IPv4 input raises
`ValueError` at construction time instead of silently auto-detecting,
so misconfiguration surfaces immediately rather than scanning the wrong
subnet.

## Command-line usage

Installing `aiodiscover` exposes an `aiodiscover` console script (also
runnable as `python -m aiodiscover`) that scans the local network and
prints the discovered hosts.

```console
$ aiodiscover
Hostname  IP           MAC
--------  -----------  -----------------
router    192.168.1.1  aa:bb:cc:dd:ee:ff
laptop    192.168.1.2  11:22:33:44:55:66

$ aiodiscover --json
[
  {"hostname": "router", "ip": "192.168.1.1", "macaddress": "aa:bb:cc:dd:ee:ff"},
  {"hostname": "laptop", "ip": "192.168.1.2", "macaddress": "11:22:33:44:55:66"}
]
```

Rows are sorted by IP. Peer-supplied hostname / MAC / IP values are
stripped of non-printable characters before display, so a hostile DHCP /
DNS responder on the LAN can't inject ANSI escapes or control bytes into
the terminal.

Common flags:

- `--format {table,json,pprint}` — pick output format (default `table`).
- `--json` — shortcut for `--format json`. Pair with `--indent N` to
  control JSON indentation.
- `-v` / `--debug` — bump logging verbosity (shortcuts for
  `--log-level INFO` / `DEBUG`).
- `--recurse` — allow recursive PTR queries (the default `--no-recurse`
  keeps queries from being forwarded to upstream public resolvers).
- `--local-ip IP` — pin discovery to the subnet of `IP` (the IPv4
  address of the interface to use). Useful when the default route
  doesn't point at the network you want to scan.
- `--version` — print the installed version and exit.

Run `aiodiscover --help` for the full reference.

## Installation

**Stable Release:** `pip install aiodiscover`<br>
**Development Head:** `pip install git+https://github.com/bluetooth-devices/aiodiscover.git`

## Documentation

For full package documentation please visit [aiodiscover.readthedocs.io](https://aiodiscover.readthedocs.io).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for information related to developing the code.

## The Four Commands You Need To Know

1. `pip install -e .[dev]`

   This will install your package in editable mode with all the required development
   dependencies (i.e. `tox`).

2. `make build`

   This will run `tox` which will run all your tests in both Python 3.7
   and Python 3.8 as well as linting your code.

3. `make clean`

   This will clean up various Python and build generated files so that you can ensure
   that you are working in a clean environment.

4. `make docs`

   This will generate and launch a web browser to view the most up-to-date
   documentation for your Python package.

#### Additional Optional Setup Steps:

- Turn your project into a GitHub repository:
  - Make an account on [github.com](https://github.com)
  - Go to [make a new repository](https://github.com/new)
  - _Recommendations:_
    - _It is strongly recommended to make the repository name the same as the Python
      package name_
    - _A lot of the following optional steps are *free* if the repository is Public,
      plus open source is cool_
  - After a GitHub repo has been created, run the commands listed under:
    "...or push an existing repository from the command line"
- Register your project with Codecov:
  - Make an account on [codecov.io](https://codecov.io)(Recommended to sign in with GitHub)
    everything else will be handled for you.
- Ensure that you have set GitHub pages to build the `gh-pages` branch by selecting the
  `gh-pages` branch in the dropdown in the "GitHub Pages" section of the repository settings.
  ([Repo Settings](https://github.com/bluetooth-devices/aiodiscover/settings))
- Register your project with PyPI:
  - Make an account on [pypi.org](https://pypi.org)
  - Go to your GitHub repository's settings and under the
    [Secrets tab](https://github.com/bluetooth-devices/aiodiscover/settings/secrets/actions),
    add a secret called `PYPI_TOKEN` with your password for your PyPI account.
    Don't worry, no one will see this password because it will be encrypted.
  - Next time you push to the branch `main` after using `bump2version`, GitHub
    actions will build and deploy your Python package to PyPI.

#### Suggested Git Branch Strategy

1. `main` is for the most up-to-date development, very rarely should you directly
   commit to this branch. GitHub Actions will run on every push and on a CRON to this
   branch but still recommended to commit to your development branches and make pull
   requests to main. If you push a tagged commit with bumpversion, this will also release to PyPI.
2. Your day-to-day work should exist on branches separate from `main`. Even if it is
   just yourself working on the repository, make a PR from your working branch to `main`
   so that you can ensure your commits don't break the development head. GitHub Actions
   will run on every push to any branch or any pull request from any branch to any other
   branch.
3. It is recommended to use "Squash and Merge" commits when committing PR's. It makes
   each set of changes to `main` atomic and as a side effect naturally encourages small
   well defined PR's.

**Apache Software License 2.0**
