#!/usr/bin/env python
import asyncio
import sys
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network
from typing import Any
from unittest.mock import patch

import aiodns
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
async def test_async_get_hostnames_no_results() -> None:
    """Verify async_get_hostnames with no results."""
    discover_hosts = discovery.DiscoverHosts()
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("172.0.0.3"), IPv4Address("172.0.0.4")]
    with (
        patch.object(
            net_data,
            "async_get_neighbours",
            return_value={},
        ),
        patch("aiodiscover.discovery.async_query_for_ptrs", return_value={}),
    ):
        hostnames = await discover_hosts.async_get_hostnames(net_data)

    assert hostnames == {}
    # We should not add failed nameservers if we get no results
    # since it could be a transient issue
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
        nameserver = resolver.nameservers[0]
        queries.append((nameserver, ips_to_lookup))
        if nameserver == str(IPv4Address("172.0.0.4")):
            return [MockReply(name="xyz.org")] * subnet_size
        return [] * subnet_size

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
