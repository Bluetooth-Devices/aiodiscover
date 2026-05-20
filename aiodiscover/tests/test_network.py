#!/usr/bin/env python
import asyncio
import subprocess
import sys
from ipaddress import IPv4Address, IPv6Address, ip_address
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiodiscover.network import (
    ResolvConfSignature,
    SystemNetworkData,
    _fill_neighbor,
    _get_macos_default_gateway,
    async_populate_arp,
    load_resolv_conf_with_signature,
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


def test_parse_resolv_conf_skips_malformed_lines() -> None:
    """Bare tokens without a value must be skipped, not crash setup."""
    resolv_conf = parse_resolv_conf(
        [
            "nameserver 1.1.1.1",
            "options",
            "nameserver",
            "",
            "   ",
            "nameserver 8.8.8.8",
        ],
    )
    assert resolv_conf == [
        IPv4Address("1.1.1.1"),
        IPv4Address("8.8.8.8"),
    ]


def test_resolv_conf_signature_returns_stat_tuple(tmp_path: Path) -> None:
    """Signature reflects mtime_ns and size of resolv.conf."""
    resolv = tmp_path / "resolv.conf"
    first_bytes = b"nameserver 1.2.3.4\n"
    resolv.write_bytes(first_bytes)
    with patch("aiodiscover.network.RESOLV_CONF_PATH", str(resolv)):
        first = resolv_conf_signature()
        assert first is not None
        assert isinstance(first, ResolvConfSignature)
        assert first.size == len(first_bytes)
        # Rewrite with different content; size changes -> signature changes.
        resolv.write_bytes(b"nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
        second = resolv_conf_signature()
        assert second is not None
        assert second != first


def test_resolv_conf_signature_missing_file_returns_none() -> None:
    """Missing resolv.conf yields None instead of raising."""
    with patch("aiodiscover.network.RESOLV_CONF_PATH", "/nonexistent/resolv.conf"):
        assert resolv_conf_signature() is None


def test_setup_defaults_nameservers_to_empty_on_windows_without_resolv_conf() -> None:
    """On Windows, a missing resolv.conf leaves nameservers as an empty list."""
    net_data = SystemNetworkData(None, local_ip="192.168.1.10")
    with (
        patch(
            "aiodiscover.network.load_resolv_conf_with_signature",
            side_effect=FileNotFoundError,
        ),
        patch("aiodiscover.network.sys") as mock_sys,
        patch("aiodiscover.network.ifaddr.get_adapters", return_value=[]),
    ):
        mock_sys.platform = "win32"
        net_data.setup()
    assert net_data.nameservers == []


def test_setup_propagates_missing_resolv_conf_on_non_windows() -> None:
    """On Linux/macOS, a missing resolv.conf still bubbles up."""
    net_data = SystemNetworkData(None, local_ip="192.168.1.10")
    with (
        patch(
            "aiodiscover.network.load_resolv_conf_with_signature",
            side_effect=FileNotFoundError,
        ),
        patch("aiodiscover.network.sys") as mock_sys,
    ):
        mock_sys.platform = "linux"
        with pytest.raises(FileNotFoundError):
            net_data.setup()


def test_load_resolv_conf_with_signature_matches_fstat(tmp_path: Path) -> None:
    """The returned signature is derived from the same fd as the parsed content."""
    resolv = tmp_path / "resolv.conf"
    payload = b"nameserver 192.168.1.53\nnameserver 1.1.1.1\n"
    resolv.write_bytes(payload)
    with patch("aiodiscover.network.RESOLV_CONF_PATH", str(resolv)):
        signature, nameservers = load_resolv_conf_with_signature()
    assert isinstance(signature, ResolvConfSignature)
    assert signature.size == len(payload)
    assert nameservers == [IPv4Address("192.168.1.53"), IPv4Address("1.1.1.1")]


def test_load_resolv_conf_with_signature_consistent_after_swap(
    tmp_path: Path,
) -> None:
    """Signature returned by each call matches the bytes returned by that call."""
    real_a = tmp_path / "a.conf"
    real_a.write_bytes(b"nameserver 1.1.1.1\n")
    real_b = tmp_path / "b.conf"
    real_b.write_bytes(b"nameserver 9.9.9.9\nnameserver 8.8.8.8\n")
    link = tmp_path / "resolv.conf"
    link.symlink_to(real_a)
    with patch("aiodiscover.network.RESOLV_CONF_PATH", str(link)):
        sig_a, ns_a = load_resolv_conf_with_signature()
        # Swap the symlink target to a different file.
        link.unlink()
        link.symlink_to(real_b)
        sig_b, ns_b = load_resolv_conf_with_signature()
    assert ns_a == [IPv4Address("1.1.1.1")]
    assert ns_b == [IPv4Address("9.9.9.9"), IPv4Address("8.8.8.8")]
    assert sig_a != sig_b


def test_load_resolv_conf_with_signature_raises_on_missing() -> None:
    """The combined loader propagates FileNotFoundError to its caller."""
    with patch("aiodiscover.network.RESOLV_CONF_PATH", "/nonexistent/resolv.conf"):
        with pytest.raises(FileNotFoundError):
            load_resolv_conf_with_signature()


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


def test_fill_neighbor_accepts_valid_entry() -> None:
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, "192.168.1.5", "aa:bb:cc:dd:ee:ff")
    assert neighbours == {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}


def test_fill_neighbor_pads_short_octets() -> None:
    """MAC octets emitted as single hex chars are zero-padded."""
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, "192.168.1.5", "a:b:c:d:e:f")
    assert neighbours == {"192.168.1.5": "0a:0b:0c:0d:0e:0f"}


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.1.1",
        "224.0.0.251",
        "0.0.0.0",  # noqa: S104 - intentional unspecified test input
    ],
)
def test_fill_neighbor_rejects_special_addresses(ip: str) -> None:
    """Loopback, link-local, multicast, unspecified addresses are skipped."""
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, ip, "aa:bb:cc:dd:ee:ff")
    assert neighbours == {}


def test_fill_neighbor_rejects_unparseable_ip() -> None:
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, "not-an-ip", "aa:bb:cc:dd:ee:ff")
    assert neighbours == {}


def test_fill_neighbor_rejects_invalid_mac() -> None:
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, "192.168.1.5", "not-a-mac")
    assert neighbours == {}


@pytest.mark.parametrize(
    "mac",
    ["00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"],
)
def test_fill_neighbor_rejects_ignored_macs(mac: str) -> None:
    neighbours: dict[str, str] = {}
    _fill_neighbor(neighbours, "192.168.1.5", mac)
    assert neighbours == {}


def test_async_populate_arp_returns_nonblocking_socket() -> None:
    """async_populate_arp returns an open, non-blocking socket and probes each ip."""
    sock = async_populate_arp(["192.0.2.1", "192.0.2.2"])
    try:
        assert sock.getblocking() is False
    finally:
        sock.close()


def test_async_populate_arp_swallows_send_errors() -> None:
    """Per-host send failures are suppressed; socket is still returned."""
    with patch("aiodiscover.network.socket.socket") as sock_cls:
        sock_instance = MagicMock()
        sock_instance.sendto.side_effect = OSError("ENETUNREACH")
        sock_cls.return_value = sock_instance
        result = async_populate_arp(["192.0.2.1", "192.0.2.2"])
    assert result is sock_instance
    assert sock_instance.sendto.call_count == 2


@pytest.mark.asyncio
async def test_async_get_neighbours_ip_route_parses_linux_keys() -> None:
    """Linux netlink NDA_DST / NDA_LLADDR attrs feed the neighbour map."""
    ip_route = MagicMock()
    ip_route.get_neighbours = MagicMock(
        return_value=[
            {
                "attrs": [
                    ("NDA_DST", "192.168.1.5"),
                    ("NDA_LLADDR", "aa:bb:cc:dd:ee:ff"),
                ],
            },
        ],
    )
    net_data = SystemNetworkData(ip_route)
    result = await net_data._async_get_neighbours_ip_route()
    assert result == {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}


@pytest.mark.asyncio
async def test_async_get_neighbours_ip_route_parses_darwin_keys() -> None:
    """pyroute2's macOS stub uses NEIGH_IP / NEIGH_LLADDR; both names parse."""
    ip_route = MagicMock()
    ip_route.get_neighbours = MagicMock(
        return_value=[
            {
                "attrs": [
                    ("NEIGH_IP", "192.168.1.6"),
                    ("NEIGH_LLADDR", "11:22:33:44:55:66"),
                    ("NEIGH_IFNAME", "en0"),
                ],
            },
        ],
    )
    net_data = SystemNetworkData(ip_route)
    result = await net_data._async_get_neighbours_ip_route()
    assert result == {"192.168.1.6": "11:22:33:44:55:66"}


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_parses_output() -> None:
    """The `arp -a -n` parser pulls ip + mac from each line."""
    arp_output = (
        b"? (192.168.1.5) at aa:bb:cc:dd:ee:ff [ether] on eth0\n"
        b"? (192.168.1.6) at 11:22:33:44:55:66 [ether] on eth0\n"
        b"incomplete line\n"
        b"? (127.0.0.1) at aa:bb:cc:dd:ee:ff on lo\n"  # loopback skipped
    )
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(arp_output, b""))
    proc.kill = AsyncMock()
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        result = await net_data._async_get_neighbours_arp()
    assert result == {
        "192.168.1.5": "aa:bb:cc:dd:ee:ff",
        "192.168.1.6": "11:22:33:44:55:66",
    }


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_timeout_returns_empty() -> None:
    """A timeout while running arp yields an empty dict, kills and reaps the proc."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.returncode = None
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        result = await net_data._async_get_neighbours_arp()
    assert result == {}
    proc.kill.assert_called_once_with()
    proc.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_timeout_survives_already_exited_proc() -> None:
    """If kill races a natural exit, ProcessLookupError is swallowed."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    proc.kill = MagicMock(side_effect=ProcessLookupError)
    proc.wait = AsyncMock()
    proc.returncode = None
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        result = await net_data._async_get_neighbours_arp()
    assert result == {}
    proc.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_cancellation_reaps_proc() -> None:
    """Caller cancellation mid-communicate kills and reaps before propagating."""
    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.returncode = None
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        with pytest.raises(asyncio.CancelledError):
            await net_data._async_get_neighbours_arp()
    proc.kill.assert_called_once_with()
    proc.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_normal_exit_does_not_kill() -> None:
    """A natural exit leaves the proc alone — no spurious kill on success."""
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    proc.returncode = 0
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(return_value=proc),
    ):
        result = await net_data._async_get_neighbours_arp()
    assert result == {}
    proc.kill.assert_not_called()
    proc.wait.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_get_neighbours_arp_handles_spawn_failure() -> None:
    """If `arp` is not installed, an OSError yields an empty dict."""
    net_data = SystemNetworkData(None)
    with patch(
        "aiodiscover.network.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=FileNotFoundError),
    ):
        result = await net_data._async_get_neighbours_arp()
    assert result == {}


@pytest.mark.asyncio
async def test_async_get_neighbours_dispatches_to_ip_route_when_available() -> None:
    """When pyroute2 is available, the netlink helper is used."""
    net_data = SystemNetworkData(MagicMock())
    expected = {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}
    with (
        patch.object(
            net_data, "_async_get_neighbours_ip_route", AsyncMock(return_value=expected)
        ),
        patch.object(net_data, "_async_get_neighbours_arp", AsyncMock()) as arp_mock,
    ):
        result = await net_data._async_get_neighbours()
    assert result == expected
    arp_mock.assert_not_called()


@pytest.mark.asyncio
async def test_async_get_neighbours_falls_back_to_arp_without_ip_route() -> None:
    """Without pyroute2 the arp command is used."""
    net_data = SystemNetworkData(None)
    expected = {"192.168.1.6": "11:22:33:44:55:66"}
    with patch.object(
        net_data, "_async_get_neighbours_arp", AsyncMock(return_value=expected)
    ):
        result = await net_data._async_get_neighbours()
    assert result == expected


@pytest.mark.asyncio
async def test_async_get_neighbours_skips_arp_populate_when_all_known() -> None:
    """If the first lookup already covers every requested ip, skip the ARP probe."""
    net_data = SystemNetworkData(MagicMock())
    cached = {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}
    with (
        patch.object(
            net_data, "_async_get_neighbours", AsyncMock(return_value=cached)
        ) as get_mock,
        patch("aiodiscover.network.async_populate_arp") as populate_mock,
        patch("aiodiscover.network.asyncio.sleep") as sleep_mock,
    ):
        result = await net_data.async_get_neighbours(["192.168.1.5"])
    assert result == cached
    assert get_mock.await_count == 1
    populate_mock.assert_not_called()
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_async_get_neighbours_repopulates_arp_for_missing_ips() -> None:
    """Missing ips trigger an ARP probe, a sleep, and a second neighbour lookup."""
    net_data = SystemNetworkData(MagicMock())
    first = {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}
    second = {
        "192.168.1.5": "aa:bb:cc:dd:ee:ff",
        "192.168.1.6": "11:22:33:44:55:66",
    }
    sock = MagicMock()
    with (
        patch.object(
            net_data, "_async_get_neighbours", AsyncMock(side_effect=[first, second])
        ),
        patch(
            "aiodiscover.network.async_populate_arp", return_value=sock
        ) as populate_mock,
        patch("aiodiscover.network.asyncio.sleep", AsyncMock()) as sleep_mock,
    ):
        result = await net_data.async_get_neighbours(["192.168.1.5", "192.168.1.6"])
    assert result == second
    populate_mock.assert_called_once_with(["192.168.1.6"])
    sleep_mock.assert_awaited_once()
    sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_async_get_neighbours_closes_arp_socket_on_cancel() -> None:
    """A CancelledError during the ARP populate sleep still closes the socket."""
    net_data = SystemNetworkData(MagicMock())
    sock = MagicMock()
    with (
        patch.object(
            net_data,
            "_async_get_neighbours",
            AsyncMock(return_value={}),
        ),
        patch("aiodiscover.network.async_populate_arp", return_value=sock),
        patch(
            "aiodiscover.network.asyncio.sleep",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await net_data.async_get_neighbours(["192.168.1.5"])
    sock.close.assert_called_once()


@pytest.mark.asyncio
async def test_async_get_neighbours_ip_route_parses_attrs() -> None:
    """The pyroute2 path extracts NDA_DST + NDA_LLADDR per neighbour."""
    ip_route = MagicMock()
    ip_route.get_neighbours.return_value = [
        {
            "attrs": [
                ("NDA_DST", "192.168.1.5"),
                ("NDA_LLADDR", "aa:bb:cc:dd:ee:ff"),
            ],
        },
        {"attrs": [("NDA_DST", "192.168.1.6")]},  # missing mac → dropped
        {"attrs": [("NDA_LLADDR", "11:22:33:44:55:66")]},  # missing ip → dropped
    ]
    net_data = SystemNetworkData(ip_route)
    result = await net_data._async_get_neighbours_ip_route()
    assert result == {"192.168.1.5": "aa:bb:cc:dd:ee:ff"}


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
