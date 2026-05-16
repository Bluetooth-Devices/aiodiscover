#!/usr/bin/env python
import asyncio
import subprocess
import sys
from ipaddress import IPv4Address, IPv6Address
from unittest.mock import MagicMock, patch

from aiodiscover.network import _get_macos_default_gateway, parse_resolv_conf

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
