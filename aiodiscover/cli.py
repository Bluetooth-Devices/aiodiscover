"""Command-line interface for aiodiscover."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pprint
import sys
from typing import TYPE_CHECKING

from . import __version__
from .discovery import DiscoverHosts

if TYPE_CHECKING:
    from collections.abc import Sequence


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the aiodiscover CLI."""
    parser = argparse.ArgumentParser(
        prog="aiodiscover",
        description=(
            "Discover hosts on the local network via ARP probing and reverse "
            "DNS (PTR) lookups."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_true",
        help="Emit results as a JSON array on stdout instead of pprint output.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indent size for JSON output (default: 2). Ignored without --json.",
    )
    parser.add_argument(
        "--log-level",
        choices=_LOG_LEVELS,
        default="WARNING",
        help="Logging verbosity (default: WARNING).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_const",
        const="INFO",
        dest="log_level",
        help="Shortcut for --log-level INFO.",
    )
    parser.add_argument(
        "--debug",
        action="store_const",
        const="DEBUG",
        dest="log_level",
        help="Shortcut for --log-level DEBUG.",
    )
    recurse = parser.add_mutually_exclusive_group()
    recurse.add_argument(
        "--no-recurse",
        dest="no_recurse",
        action="store_true",
        default=True,
        help=(
            "Set the DNS no-recursion flag on PTR queries (default). "
            "Avoids leaking queries to upstream public resolvers."
        ),
    )
    recurse.add_argument(
        "--recurse",
        dest="no_recurse",
        action="store_false",
        help="Allow recursive DNS PTR queries (the aiodns/pycares default).",
    )
    return parser


async def _discover(no_recurse: bool) -> list[dict[str, str]]:
    async with DiscoverHosts(no_recurse=no_recurse) as discover_hosts:
        return await discover_hosts.async_discover()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the aiodiscover CLI. Returns a shell exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level))

    try:
        hosts = asyncio.run(_discover(no_recurse=args.no_recurse))
    except KeyboardInterrupt:
        return 130

    if args.json:
        json.dump(hosts, sys.stdout, indent=args.indent, sort_keys=True)
        sys.stdout.write("\n")
    else:
        pprint.pprint(hosts)
    return 0
