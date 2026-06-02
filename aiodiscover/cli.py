"""Command-line interface for aiodiscover."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import pprint
import sys
from typing import TYPE_CHECKING

from . import __version__
from ._sanitize import MAX_HOSTNAME_LEN, MAX_IP_LEN, MAX_MAC_LEN, safe_label_str
from .discovery import HOSTNAME, IP_ADDRESS, MAC_ADDRESS, DiscoverHosts

if TYPE_CHECKING:
    from collections.abc import Sequence


_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
_FORMATS = ("table", "json", "pprint")
_TABLE_COLUMNS = (("Hostname", HOSTNAME), ("IP", IP_ADDRESS), ("MAC", MAC_ADDRESS))
_FIELD_LIMITS = {
    HOSTNAME: MAX_HOSTNAME_LEN,
    IP_ADDRESS: MAX_IP_LEN,
    MAC_ADDRESS: MAX_MAC_LEN,
}


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
        "-f",
        "--format",
        choices=_FORMATS,
        default="table",
        help="Output format (default: table).",
    )
    parser.add_argument(
        "-j",
        "--json",
        action="store_const",
        const="json",
        dest="format",
        help="Shortcut for --format json.",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indent size for JSON output (default: 2). Ignored without --format json.",
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
    parser.add_argument(
        "--local-ip",
        dest="local_ip",
        default=None,
        help=(
            "IPv4 address of the interface to discover on. Pins discovery to the "
            "subnet of this address instead of using the default route."
        ),
    )
    return parser


def _sanitize_host(host: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``host`` with each known field length-capped + printable-only."""
    return {
        key: safe_label_str(host.get(key, ""), limit)
        for key, limit in _FIELD_LIMITS.items()
    }


def _ip_sort_key(host: dict[str, str]) -> tuple[int, object]:
    """Sort key that orders IPv4 numerically, IPv6 after, malformed last."""
    raw = host.get(IP_ADDRESS, "")
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return (2, raw)
    return (0 if isinstance(addr, ipaddress.IPv4Address) else 1, addr)


def _render_table(hosts: list[dict[str, str]]) -> str:
    """Render hosts as an aligned three-column table sorted by IP."""
    headers = [label for label, _ in _TABLE_COLUMNS]
    rows = [[host[field] for _, field in _TABLE_COLUMNS] for host in hosts]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        if rows
        else len(headers[i])
        for i in range(len(headers))
    ]
    fmt = "  ".join(f"{{: <{w}}}" for w in widths)
    lines = [
        fmt.format(*headers).rstrip(),
        fmt.format(*("-" * w for w in widths)).rstrip(),
    ]
    lines.extend(fmt.format(*row).rstrip() for row in rows)
    return "\n".join(lines) + "\n"


async def _discover(no_recurse: bool, local_ip: str | None) -> list[dict[str, str]]:
    async with DiscoverHosts(
        no_recurse=no_recurse, local_ip=local_ip
    ) as discover_hosts:
        return await discover_hosts.async_discover()


def main(argv: Sequence[str] | None = None) -> int:
    """Run the aiodiscover CLI. Returns a shell exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level))

    try:
        hosts = asyncio.run(
            _discover(no_recurse=args.no_recurse, local_ip=args.local_ip)
        )
    except KeyboardInterrupt:
        return 130

    sanitized = sorted((_sanitize_host(h) for h in hosts), key=_ip_sort_key)

    if args.format == "json":
        json.dump(sanitized, sys.stdout, indent=args.indent, sort_keys=True)
        sys.stdout.write("\n")
    elif args.format == "pprint":
        pprint.pprint(sanitized)
    else:
        sys.stdout.write(_render_table(sanitized))
    return 0
