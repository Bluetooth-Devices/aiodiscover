#!/usr/bin/env python
import asyncio
import subprocess
import sys
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aiodiscover.network import (
    _get_macos_default_gateway,
    parse_resolv_conf,
    resolv_conf_signature,
)

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


def test_resolv_conf_signature_returns_stat_tuple(tmp_path: Path) -> None:
    """Signature reflects mtime_ns and size of resolv.conf."""
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 1.2.3.4\n")
    with patch("aiodiscover.network.RESOLV_CONF_PATH", str(resolv)):
        first = resolv_conf_signature()
        assert first is not None
        assert first[1] == len("nameserver 1.2.3.4\n")
        # Rewrite with different content; size changes -> signature changes.
        resolv.write_text("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
        second = resolv_conf_signature()
        assert second is not None
        assert second != first


def test_resolv_conf_signature_missing_file_returns_none() -> None:
    """Missing resolv.conf yields None instead of raising."""
    with patch(
        "aiodiscover.network.RESOLV_CONF_PATH", "/nonexistent/resolv.conf"
    ):
        assert resolv_conf_signature() is None


ROUTE_OUTPUT_WITH_GATEWAY = """\
   route to: default
destination: default
       mask: default
    gateway: 192.168.1.1
  interface: en0
      flags: <UP,GATEWAY,DONE,STATIC,PRMRY>
"""

ROUTE_OUTPUT_NO_GATEWAY = """\
   route to: default
destination: default
       mask: default
  interface: utun5
      flags: <UP,DONE,CLONING,STATIC,GLOBAL>
"""


def _mock_run(stdout: str, returncode: int = 0) -> MagicMock:
    mock = MagicMock()
    mock.stdout = stdout
    mock.returncode = returncode
    return mock


def test_get_macos_default_gateway_parses_gateway_line() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        return_value=_mock_run(ROUTE_OUTPUT_WITH_GATEWAY),
    ):
        assert _get_macos_default_gateway() == "192.168.1.1"


def test_get_macos_default_gateway_no_gateway_line() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        return_value=_mock_run(ROUTE_OUTPUT_NO_GATEWAY),
    ):
        assert _get_macos_default_gateway() is None


def test_get_macos_default_gateway_nonzero_exit() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        return_value=_mock_run("", returncode=1),
    ):
        assert _get_macos_default_gateway() is None


def test_get_macos_default_gateway_subprocess_error() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="route", timeout=2),
    ):
        assert _get_macos_default_gateway() is None


def test_get_macos_default_gateway_oserror() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        side_effect=FileNotFoundError(),
    ):
        assert _get_macos_default_gateway() is None


def test_get_macos_default_gateway_ipv6_strips_zone() -> None:
    """IPv6 default gateways may include a zone suffix like `%en0`."""
    output = (
        "   route to: default\n"
        "destination: default\n"
        "    gateway: fe80::1%en0\n"
        "  interface: en0\n"
    )
    with patch(
        "aiodiscover.network.subprocess.run",
        return_value=_mock_run(output),
    ) as mock_run:
        assert _get_macos_default_gateway("inet6") == "fe80::1"
    # Family argument must reach the route command.
    args = mock_run.call_args[0][0]
    assert "-inet6" in args


def test_get_macos_default_gateway_default_family_is_inet() -> None:
    with patch(
        "aiodiscover.network.subprocess.run",
        return_value=_mock_run(ROUTE_OUTPUT_WITH_GATEWAY),
    ) as mock_run:
        _get_macos_default_gateway()
    args = mock_run.call_args[0][0]
    assert "-inet" in args
    assert "-inet6" not in args


@pytest.mark.skipif(
    sys.platform != "darwin", reason="route -n get default is macOS-specific"
)
def test_get_macos_default_gateway_e2e() -> None:
    """End-to-end: actually run `route -n get default` and verify parsing."""
    result = _get_macos_default_gateway()
    if result is None:
        # No default gateway (e.g. VPN-only default route or no network) is a
        # valid outcome; the function must not raise.
        return
    # Anything returned must be a parseable IP address.
    ip_address(result)
