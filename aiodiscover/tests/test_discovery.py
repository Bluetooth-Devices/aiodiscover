#!/usr/bin/env python
import asyncio
import sys
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiodns
import pycares
import pytest

from aiodiscover import discovery
from aiodiscover.network import SystemNetworkData

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@dataclass
class MockReply:
    name: str


@pytest.mark.asyncio
async def test_async_discover_hosts() -> None:
    """Verify discover hosts does not throw."""
    discover_hosts = discovery.DiscoverHosts()
    with patch.object(discovery, "MAX_ADDRESSES", 16):
        hosts = await discover_hosts.async_discover()
    assert isinstance(hosts, list)


@pytest.mark.asyncio
async def test_async_discover_hosts_with_dns_mock() -> None:
    """Verify discover hosts does not throw."""
    discover_hosts = discovery.DiscoverHosts()
    with (
        patch.object(discovery, "MAX_ADDRESSES", 2),
        patch(
            "aiodiscover.discovery.dns_message_short_hostname",
            return_value="router",
        ),
    ):
        hosts = await discover_hosts.async_discover()
    assert isinstance(hosts, list)


@pytest.mark.asyncio
async def test_async_discover_hosts_with_dns_mock_neighbor_mock() -> None:
    """Verify discover hosts does not throw."""
    discover_hosts = discovery.DiscoverHosts()

    async def _async_get_hostnames(sys_network_data: Any) -> dict[str, str]:
        return {"1.2.3.4": "router", "4.5.5.6": "any"}

    discover_hosts.async_get_hostnames = _async_get_hostnames  # type: ignore
    with (
        patch(
            "aiodiscover.network.SystemNetworkData.async_get_neighbours",
            return_value={
                "1.2.3.4": "aa:bb:cc:dd:ee:ff",
                "4.5.5.6": "ff:bb:cc:0d:ee:ff",
            },
        ),
        patch(
            "aiodiscover.network.get_network",
            return_value=IPv4Network("1.2.3.0/24", False),
        ),
    ):
        hosts = await discover_hosts.async_discover()

    assert hosts == [
        {"hostname": "router", "ip": "1.2.3.4", "macaddress": "aa:bb:cc:dd:ee:ff"},
        {"hostname": "any", "ip": "4.5.5.6", "macaddress": "ff:bb:cc:0d:ee:ff"},
    ]


@pytest.mark.asyncio
async def test_async_query_for_ptrs() -> None:
    """Test async_query_for_ptrs handles missing ips."""
    loop = asyncio.get_running_loop()
    count = 0

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        future = loop.create_future()
        if count == 2:
            future.set_exception(Exception("test"))
        else:
            future.set_result(MockReply(name=f"name{count}"))
        return future

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0),
        patch("aiodiscover.discovery.DNSResolver.query", mock_query),
    ):
        resolver = aiodns.DNSResolver(timeout=0)
        resolver.nameservers = ["192.168.107.1"]
        response = await discovery.async_query_for_ptrs(
            resolver,
            [
                IPv4Address("192.168.107.2"),
                IPv4Address("192.168.107.3"),
                IPv4Address("192.168.107.4"),
            ],
        )

    assert len(response) == 3
    assert response[0].name == "name1"  # type: ignore
    assert response[1] is None  # type: ignore
    assert response[2].name == "name3"  # type: ignore


@pytest.mark.asyncio
async def test_nameservers_excludes_router_when_in_network_nameserver() -> None:
    """Verifynameservers excludes the router when there is an in-network nameserver."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("192.168.0.254"), IPv4Address("172.0.0.4")]
    with patch.object(
        net_data,
        "async_get_neighbours",
        return_value={"192.168.0.1": "AA:BB:CC:DD:EE:FF"},
    ):
        assert await discover_hosts._async_get_nameservers(net_data) == [
            IPv4Address("192.168.0.254"),
            IPv4Address("172.0.0.4"),
        ]


@pytest.mark.asyncio
async def test_nameservers_includes_router_no_in_network_nameserver() -> None:
    """Verify nameservers includes the router when no in-network nameserver and it responds to ARP."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    with patch.object(
        net_data,
        "async_get_neighbours",
        return_value={"192.168.0.1": "AA:BB:CC:DD:EE:FF"},
    ):
        assert await discover_hosts._async_get_nameservers(net_data) == [
            IPv4Address("172.0.0.3"),
            IPv4Address("172.0.0.4"),
            IPv4Address("192.168.0.1"),
        ]


@pytest.mark.asyncio
async def test_nameservers_includes_router_no_in_network_nameserver_no_arp() -> None:
    """Verify nameservers excludes the router when no in-network nameserver and no ARP response."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    with patch.object(
        net_data,
        "async_get_neighbours",
        return_value={},
    ):
        assert await discover_hosts._async_get_nameservers(net_data) == [
            IPv4Address("172.0.0.3"),
            IPv4Address("172.0.0.4"),
        ]


@pytest.mark.asyncio
async def test_async_query_for_ptrs_chunked() -> None:
    """Test async_query_for_ptrs chunkeds."""
    loop = asyncio.get_running_loop()
    count = 0

    @dataclass
    class MockReply:
        name: str

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        nonlocal count
        count += 1
        future = loop.create_future()
        if count == 2:
            future.set_exception(Exception("test"))
        else:
            future.set_result(MockReply(name=f"name{count}"))
        return future

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0),
        patch("aiodiscover.discovery.DNSResolver.query", mock_query),
        patch.object(discovery, "QUERY_BUCKET_SIZE", 1),
    ):
        resolver = aiodns.DNSResolver(timeout=0)
        resolver.nameservers = ["192.168.107.1"]
        response = await discovery.async_query_for_ptrs(
            resolver,
            [
                IPv4Address("192.168.107.2"),
                IPv4Address("192.168.107.3"),
                IPv4Address("192.168.107.4"),
            ],
        )

    assert len(response) == 3
    assert response[0].name == "name1"  # type: ignore
    assert response[1] is None
    assert response[2].name == "name3"  # type: ignore


@pytest.mark.asyncio
async def test_async_query_for_ptrs_pending_futures_marked_none() -> None:
    """Futures that never complete are cancelled and recorded as failures."""
    loop = asyncio.get_running_loop()
    count = 0
    pending_future: asyncio.Future[Any] | None = None

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        nonlocal count, pending_future
        count += 1
        future = loop.create_future()
        if count == 2:
            # Never resolve — simulates a wedged resolver / black-holed UDP.
            pending_future = future
        else:
            future.set_result(MockReply(name=f"name{count}"))
        return future

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0),
        patch("aiodiscover.discovery.DNSResolver.query", mock_query),
    ):
        resolver = aiodns.DNSResolver(timeout=0)
        resolver.nameservers = ["192.168.107.1"]
        response = await discovery.async_query_for_ptrs(
            resolver,
            [
                IPv4Address("192.168.107.2"),
                IPv4Address("192.168.107.3"),
                IPv4Address("192.168.107.4"),
            ],
        )

    assert len(response) == 3
    assert response[0].name == "name1"  # type: ignore
    assert response[1] is None
    assert response[2].name == "name3"  # type: ignore
    assert pending_future is not None
    assert pending_future.cancelled()


@pytest.mark.asyncio
async def test_async_get_hostnames_no_results() -> None:
    """Verify async_get_hostnames with no results."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    subnet_size = len(list(net_data.network.hosts()))
    with (
        patch.object(
            net_data,
            "async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[None] * subnet_size,
        ),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {}
    # We should not add failed nameservers if we get no results
    # since it could be a transient issue
    assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_async_get_hostnames_silent_failure_is_blacklisted() -> None:
    """Cache a nameserver as failed when every PTR response was None."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)

    async def _mock_query_for_ptrs(
        resolver: aiodns.DNSResolver,
        ips_to_lookup: list[IPv4Address],
    ) -> Any:
        nameserver = resolver.nameservers[0].split(":", 1)[0]
        if nameserver == str(IPv4Address("172.0.0.4")):
            return [MockReply(name="xyz.org")] * subnet_size
        return [None] * subnet_size

    with (
        patch.object(net_data, "async_get_neighbours", return_value={}),
        patch("aiodiscover.discovery.async_query_for_ptrs", _mock_query_for_ptrs),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {str(ip): "xyz" for ip in hosts}
    assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}


@pytest.mark.asyncio
async def test_async_get_hostnames_all_silent_does_not_blacklist() -> None:
    """Leave the failed-nameserver cache empty when no nameserver succeeded."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    subnet_size = len(list(net_data.network.hosts()))

    with (
        patch.object(net_data, "async_get_neighbours", return_value={}),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[None] * subnet_size,
        ),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {}
    assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_async_get_hostnames_all_responding() -> None:
    """Verify async_get_hostnames with responses for all IPs."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)
    with (
        patch.object(
            net_data,
            "async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[MockReply(name="xyz.org")] * subnet_size,
        ),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {str(ip): "xyz" for ip in hosts}
    assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_async_get_hostnames_partial_responding() -> None:
    """Verify async_get_hostnames with responses for some IPs."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)
    assert subnet_size == 2
    with (
        patch.object(
            net_data,
            "async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[MockReply(name="xyz.org"), None],
        ),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {
        "192.168.0.0": "xyz",
    }
    assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_async_get_hostnames_first_nameserver_fails() -> None:
    """Verify async_get_hostnames when the first nameserver fails."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)

    queries: list[tuple[str, list[IPv4Address]]] = []

    async def _mock_query_for_ptrs(
        resolver: aiodns.DNSResolver,
        ips_to_lookup: list[IPv4Address],
    ) -> Any:
        # pycares 5+ stringifies nameservers as "<ip>:<port>"; older versions
        # return the bare "<ip>". Normalise to bare IPv4 for the assertions.
        nameserver = resolver.nameservers[0].split(":", 1)[0]
        queries.append((nameserver, ips_to_lookup))
        if nameserver == str(IPv4Address("172.0.0.4")):
            return [MockReply(name="xyz.org")] * subnet_size
        # Real async_query_for_ptrs returns one slot per IP — None on a
        # failed lookup, not an empty list.
        return [None] * subnet_size

    with (
        patch.object(
            net_data,
            "async_get_neighbours",
            return_value={},
        ),
        patch("aiodiscover.discovery.async_query_for_ptrs", _mock_query_for_ptrs),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

        assert queries == [
            (str(IPv4Address("172.0.0.3")), hosts),
            (str(IPv4Address("172.0.0.4")), hosts),
        ]

        assert hostnames == {str(ip): "xyz" for ip in hosts}
        assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}

        queries.clear()
        # Now run again, and we should remember the failed nameserver
        hostnames = await discover_hosts.async_get_hostnames(net_data)

        assert queries == [
            (str(IPv4Address("172.0.0.4")), hosts),
        ]

        assert hostnames == {str(ip): "xyz" for ip in hosts}
        assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}

        discover_hosts._failed_nameservers.clear()
        queries.clear()

        # Now run again, after clearing the failed nameservers
        hostnames = await discover_hosts.async_get_hostnames(net_data)

        assert queries == [
            (str(IPv4Address("172.0.0.3")), hosts),
            (str(IPv4Address("172.0.0.4")), hosts),
        ]

        assert hostnames == {str(ip): "xyz" for ip in hosts}
        assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}


@pytest.mark.asyncio
async def test_silent_nameserver_timeout_is_blacklisted() -> None:
    """Pin: a fully silent nameserver must land in _failed_nameservers."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)

    async def _mock_query_for_ptrs(
        resolver: aiodns.DNSResolver,
        ips_to_lookup: list[IPv4Address],
    ) -> Any:
        nameserver = resolver.nameservers[0].split(":", 1)[0]
        if nameserver == str(IPv4Address("172.0.0.4")):
            return [MockReply(name="xyz.org")] * subnet_size
        # Real shape returned by async_query_for_ptrs when a resolver times
        # out on every IP: a list of Nones, one slot per requested IP.
        return [None] * subnet_size

    with (
        patch.object(net_data, "async_get_neighbours", return_value={}),
        patch("aiodiscover.discovery.async_query_for_ptrs", _mock_query_for_ptrs),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {str(ip): "xyz" for ip in hosts}
    assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}


@pytest.mark.asyncio
async def test_silent_nameserver_skipped_on_second_run() -> None:
    """Pin: a silently-failed nameserver must not be queried on the next run."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/31")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    hosts = list(net_data.network.hosts())
    subnet_size = len(hosts)

    queries: list[str] = []

    async def _mock_query_for_ptrs(
        resolver: aiodns.DNSResolver,
        ips_to_lookup: list[IPv4Address],
    ) -> Any:
        nameserver = resolver.nameservers[0].split(":", 1)[0]
        queries.append(nameserver)
        if nameserver == str(IPv4Address("172.0.0.4")):
            return [MockReply(name="xyz.org")] * subnet_size
        return [None] * subnet_size

    with (
        patch.object(net_data, "async_get_neighbours", return_value={}),
        patch("aiodiscover.discovery.async_query_for_ptrs", _mock_query_for_ptrs),
    ):
        await discover_hosts.async_get_hostnames(net_data)
        queries.clear()
        await discover_hosts.async_get_hostnames(net_data)

    assert queries == [str(IPv4Address("172.0.0.4"))]


@pytest.mark.asyncio
async def test_cache_clear() -> None:
    """Verify async_get_hostnames when the first nameserver fails."""
    loop = asyncio.get_running_loop()
    with patch.object(loop, "time", return_value=0) as mock_time:
        discover_hosts = discovery.DiscoverHosts()
        net_data = SystemNetworkData(None, None)
        net_data.router_ip = IPv4Address("192.168.0.1")
        net_data.network = IPv4Network("192.168.0.0/31")
        net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
        discover_hosts._failed_nameservers = {IPv4Address("172.0.0.3")}
        assert discover_hosts._last_cache_clear == 0
        discover_hosts._cleanup_cache()
        assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}
        mock_time.return_value = discovery.CACHE_CLEAR_INTERVAL - 10
        discover_hosts._cleanup_cache()
        assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}
        mock_time.return_value = discovery.CACHE_CLEAR_INTERVAL + 10
        discover_hosts._cleanup_cache()
        assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_reload_on_resolv_conf_change() -> None:
    """Verify async_discover reloads system network data when resolv.conf changes."""
    discover_hosts = discovery.DiscoverHosts()

    net_data_1 = SystemNetworkData(None, None)
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = [IPv4Address("192.168.0.254")]

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.99")]

    setup_results = [net_data_1, net_data_2]

    def fake_setup() -> SystemNetworkData:
        return setup_results.pop(0)

    signature_calls = iter([(1, 100), (2, 100)])

    def fake_sig() -> tuple[int, int]:
        return next(signature_calls)

    subnet_size = len(list(net_data_1.network.hosts()))

    with (
        patch.object(discover_hosts, "_setup_sys_network_data", fake_setup),
        patch("aiodiscover.discovery.resolv_conf_signature", fake_sig),
        patch.object(discovery, "MAX_ADDRESSES", 1024),
        patch(
            "aiodiscover.network.SystemNetworkData.async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[None] * subnet_size,
        ),
    ):
        await discover_hosts.async_discover()
        assert discover_hosts._sys_network_data is net_data_1

        discover_hosts._failed_nameservers.add(IPv4Address("172.0.0.3"))

        await discover_hosts.async_discover()
        assert discover_hosts._sys_network_data is net_data_2
        assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_no_reload_when_resolv_conf_unchanged() -> None:
    """Verify async_discover keeps cached data and failed cache when resolv.conf is stable."""
    discover_hosts = discovery.DiscoverHosts()

    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("192.168.0.254")]

    call_count = 0

    def fake_setup() -> SystemNetworkData:
        nonlocal call_count
        call_count += 1
        return net_data

    subnet_size = len(list(net_data.network.hosts()))

    with (
        patch.object(discover_hosts, "_setup_sys_network_data", fake_setup),
        patch(
            "aiodiscover.discovery.resolv_conf_signature",
            return_value=(7, 200),
        ),
        patch.object(discovery, "MAX_ADDRESSES", 1024),
        patch(
            "aiodiscover.network.SystemNetworkData.async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[None] * subnet_size,
        ),
    ):
        await discover_hosts.async_discover()
        discover_hosts._failed_nameservers.add(IPv4Address("172.0.0.3"))
        await discover_hosts.async_discover()

    assert call_count == 1
    assert discover_hosts._sys_network_data is net_data
    assert discover_hosts._failed_nameservers == {IPv4Address("172.0.0.3")}


@pytest.mark.asyncio
async def test_reload_when_resolv_conf_appears() -> None:
    """Verify async_discover reloads when resolv.conf transitions from missing to present."""
    discover_hosts = discovery.DiscoverHosts()

    net_data_1 = SystemNetworkData(None, None)
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = []

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.254")]

    setup_results = [net_data_1, net_data_2]

    def fake_setup() -> SystemNetworkData:
        return setup_results.pop(0)

    signature_calls = iter([None, (5, 80)])

    def fake_sig() -> tuple[int, int] | None:
        return next(signature_calls)

    subnet_size = len(list(net_data_2.network.hosts()))

    with (
        patch.object(discover_hosts, "_setup_sys_network_data", fake_setup),
        patch("aiodiscover.discovery.resolv_conf_signature", fake_sig),
        patch.object(discovery, "MAX_ADDRESSES", 1024),
        patch(
            "aiodiscover.network.SystemNetworkData.async_get_neighbours",
            return_value={},
        ),
        patch(
            "aiodiscover.discovery.async_query_for_ptrs",
            return_value=[None] * subnet_size,
        ),
    ):
        await discover_hosts.async_discover()
        assert discover_hosts._sys_network_data is net_data_1

        discover_hosts._failed_nameservers.add(IPv4Address("172.0.0.3"))

        await discover_hosts.async_discover()
        assert discover_hosts._sys_network_data is net_data_2
        assert discover_hosts._failed_nameservers == set()


@pytest.mark.asyncio
async def test_no_recurse_default_true() -> None:
    """Verify that no_recurse defaults to True and sets ARES_FLAG_NORECURSE."""
    with patch("aiodiscover.discovery.DNSResolver") as mock_resolver_class:
        mock_resolver = MagicMock()
        mock_resolver_class.return_value = mock_resolver

        # Create DiscoverHosts with default no_recurse=True
        discovery.DiscoverHosts()

        # Verify DNSResolver was called with flags=ARES_FLAG_NORECURSE
        mock_resolver_class.assert_called_once_with(
            timeout=discovery.DNS_RESPONSE_TIMEOUT, flags=pycares.ARES_FLAG_NORECURSE
        )


@pytest.mark.asyncio
async def test_no_recurse_false() -> None:
    """Verify that no_recurse=False does not set ARES_FLAG_NORECURSE."""
    with patch("aiodiscover.discovery.DNSResolver") as mock_resolver_class:
        mock_resolver = MagicMock()
        mock_resolver_class.return_value = mock_resolver

        # Create DiscoverHosts with no_recurse=False
        discovery.DiscoverHosts(no_recurse=False)

        # Verify DNSResolver was called without flags
        mock_resolver_class.assert_called_once_with(
            timeout=discovery.DNS_RESPONSE_TIMEOUT
        )


def test_decode_idna_returns_unicode_for_valid_punycode() -> None:
    """`xn--` punycode is decoded to the original unicode label."""
    # "xn--bcher-kva" → "bücher"
    assert discovery.decode_idna("xn--bcher-kva") == "bücher"


def test_decode_idna_falls_back_to_input_on_bad_input() -> None:
    """Garbled punycode raises UnicodeError; we fall back to the input."""
    discovery.decode_idna.cache_clear()
    assert discovery.decode_idna("xn--not-valid-punycode-!!!") == (
        "xn--not-valid-punycode-!!!"
    )


def test_dns_message_short_hostname_handles_none() -> None:
    assert discovery.dns_message_short_hostname(None) is None


def test_dns_message_short_hostname_strips_domain() -> None:
    assert discovery.dns_message_short_hostname(MockReply("host.example.com")) == "host"


def test_dns_message_short_hostname_decodes_idna() -> None:
    """A punycode reply name is decoded before the short-host slice is taken."""
    assert (
        discovery.dns_message_short_hostname(MockReply("xn--bcher-kva.example.com"))
        == "bücher"
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("xn--zckzah.example.com", "テスト"),
        ("xn--wgv71a.example.com", "日本"),
        ("xn--0zwm56d.example.com", "测试"),
        ("xn--3e0b707e.example.com", "한국"),
        ("xn--zck4a3c.example.com", "ホスト"),
    ],
)
def test_dns_message_short_hostname_decodes_east_asian_idna(
    name: str, expected: str
) -> None:
    """East Asian punycode labels survive LDH validation and decode."""
    assert discovery.dns_message_short_hostname(MockReply(name)) == expected


@pytest.mark.parametrize(
    "name",
    [
        "evil\nhost.example.com",
        "evil\rhost.example.com",
        "evil\thost.example.com",
        "evil host.example.com",
        "evil\x00host.example.com",
        "<script>.example.com",
        "../etc/passwd",
        "-leading-hyphen.example.com",
        "trailing-hyphen-.example.com",
        "",
        ".example.com",
        "a" * 64 + ".example.com",
        "foo;rm -rf /.example.com",
    ],
)
def test_dns_message_short_hostname_rejects_invalid_labels(name: str) -> None:
    """A non-LDH PTR label is dropped rather than propagated to callers."""
    assert discovery.dns_message_short_hostname(MockReply(name)) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("host.example.com", "host"),
        ("a", "a"),
        ("a" * 63, "a" * 63),
        ("h0st-1.example.com", "h0st-1"),
        ("HOST.example.com", "HOST"),
    ],
)
def test_dns_message_short_hostname_accepts_valid_labels(
    name: str, expected: str
) -> None:
    """LDH-compliant labels survive unchanged."""
    assert discovery.dns_message_short_hostname(MockReply(name)) == expected


@pytest.mark.asyncio
async def test_no_recurse_true_explicit() -> None:
    """Verify that explicit no_recurse=True sets ARES_FLAG_NORECURSE."""
    with patch("aiodiscover.discovery.DNSResolver") as mock_resolver_class:
        mock_resolver = MagicMock()
        mock_resolver_class.return_value = mock_resolver

        # Create DiscoverHosts with explicit no_recurse=True
        discovery.DiscoverHosts(no_recurse=True)

        # Verify DNSResolver was called with flags=ARES_FLAG_NORECURSE
        mock_resolver_class.assert_called_once_with(
            timeout=discovery.DNS_RESPONSE_TIMEOUT, flags=pycares.ARES_FLAG_NORECURSE
        )


@pytest.mark.asyncio
async def test_close_releases_resolver_and_ip_route() -> None:
    """close() awaits the resolver and closes a held pyroute2 IPRoute."""
    discover_hosts = discovery.DiscoverHosts()
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    discover_hosts._resolver = fake_resolver

    fake_ip_route = MagicMock()
    fake_net_data = MagicMock(ip_route=fake_ip_route)
    discover_hosts._sys_network_data = fake_net_data

    await discover_hosts.close()

    fake_resolver.close.assert_awaited_once()
    fake_ip_route.close.assert_called_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_close_when_no_sys_network_data() -> None:
    """close() works even if async_discover was never called."""
    discover_hosts = discovery.DiscoverHosts()
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    discover_hosts._resolver = fake_resolver

    await discover_hosts.close()

    fake_resolver.close.assert_awaited_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_close_tolerates_ip_route_close_error() -> None:
    """A pyroute2 close exception does not propagate out of close()."""
    discover_hosts = discovery.DiscoverHosts()
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    discover_hosts._resolver = fake_resolver

    fake_ip_route = MagicMock()
    fake_ip_route.close.side_effect = OSError("already closed")
    fake_net_data = MagicMock(ip_route=fake_ip_route)
    discover_hosts._sys_network_data = fake_net_data

    await discover_hosts.close()

    fake_resolver.close.assert_awaited_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_async_context_manager_closes_resolver() -> None:
    """`async with DiscoverHosts()` calls close() on exit."""
    discover_hosts = discovery.DiscoverHosts()
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    discover_hosts._resolver = fake_resolver

    async with discover_hosts as ctx:
        assert ctx is discover_hosts

    fake_resolver.close.assert_awaited_once()
