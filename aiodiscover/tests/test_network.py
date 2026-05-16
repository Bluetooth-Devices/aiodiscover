#!/usr/bin/env python
import asyncio
import sys
from ipaddress import IPv4Address, IPv6Address

from aiodiscover.network import parse_resolv_conf

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def test_parse_resolv_conf() -> None:
    """Verify parse_resolv_conf."""
    resolv_conf = parse_resolv_conf(
        [
            "# This is a comment",
            "; This is a comment",
            "  ; This is a comment",
            "nameserver 3.3.4.3",
            "   nameserver   32.2.1.1   ",
            " nameserver        2001:4860:4860::8888",
        ],
    )
    assert resolv_conf == [
        IPv4Address("3.3.4.3"),
        IPv4Address("32.2.1.1"),
        IPv6Address("2001:4860:4860::8888"),
    ]


def test_netifaces_namespace_available() -> None:
    """
    The netifaces top-level module must remain importable.

    network.py imports `netifaces` inside a `with suppress(Exception)` block to
    look up the default gateway on macOS. After swapping the dependency to
    netifaces-plus, that import is silently swallowed if the shim ever drops
    the top-level `netifaces` name, which would leave the macOS gateway lookup
    permanently dead with no CI signal. This test fails loudly in that case.
    """
    import netifaces

    assert hasattr(netifaces, "gateways")
    assert hasattr(netifaces, "AF_INET")
    assert callable(netifaces.gateways)
