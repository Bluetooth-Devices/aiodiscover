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
    async with discovery.DiscoverHosts() as discover_hosts:
        with patch.object(discovery, "MAX_ADDRESSES", 16):
            hosts = await discover_hosts.async_discover()
    assert isinstance(hosts, list)


@pytest.mark.asyncio
async def test_async_discover_hosts_with_dns_mock() -> None:
    """Verify discover hosts does not throw."""
    async with discovery.DiscoverHosts() as discover_hosts:
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
    async with discovery.DiscoverHosts() as discover_hosts:

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

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]
    with patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0):
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
async def test_nameservers_excludes_router_when_in_network_nameserver(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verifynameservers excludes the router when there is an in-network nameserver."""
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
async def test_nameservers_includes_router_no_in_network_nameserver(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify nameservers includes the router when no in-network nameserver and it responds to ARP."""
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
async def test_nameservers_includes_router_no_in_network_nameserver_no_arp(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify nameservers excludes the router when no in-network nameserver and no ARP response."""
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

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]
    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0),
        patch.object(discovery, "QUERY_BUCKET_SIZE", 1),
    ):
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

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]
    with patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 0):
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
async def test_async_query_for_ptrs_cancellation_releases_resolver() -> None:
    """Outer-task cancellation flushes pycares state and any in-flight futures."""
    loop = asyncio.get_running_loop()
    submitted: list[asyncio.Future[Any]] = []

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        future = loop.create_future()
        submitted.append(future)
        return future  # never resolves — caller must cancel us out of it

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 10),
        patch.object(discovery, "QUERY_BUCKET_SIZE", 2),
    ):
        task = asyncio.create_task(
            discovery.async_query_for_ptrs(
                resolver,
                [
                    IPv4Address("192.168.107.2"),
                    IPv4Address("192.168.107.3"),
                ],
            )
        )
        # Let the task enter asyncio.wait before cancelling.
        for _ in range(5):
            await asyncio.sleep(0)
            if submitted:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert resolver.cancel.call_count == 1
    assert submitted
    assert all(future.done() for future in submitted)


@pytest.mark.asyncio
async def test_async_query_for_ptrs_cancellation_retrieves_done_exception() -> None:
    """Done-with-exception futures get their exception retrieved on cancellation."""
    loop = asyncio.get_running_loop()
    submitted: list[asyncio.Future[Any]] = []

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        future = loop.create_future()
        if not submitted:
            future.set_exception(RuntimeError("boom"))
        submitted.append(future)
        return future

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 10),
        patch.object(discovery, "QUERY_BUCKET_SIZE", 2),
    ):
        task = asyncio.create_task(
            discovery.async_query_for_ptrs(
                resolver,
                [
                    IPv4Address("192.168.107.2"),
                    IPv4Address("192.168.107.3"),
                ],
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
            if len(submitted) == 2:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert resolver.cancel.call_count == 1
    assert submitted[0].done()
    assert not submitted[0].cancelled()
    assert submitted[0].exception() is not None
    assert submitted[1].cancelled()


@pytest.mark.asyncio
async def test_async_query_for_ptrs_cancellation_skips_already_cancelled() -> None:
    """Already-cancelled futures in in_flight are left alone on cancellation cleanup."""
    loop = asyncio.get_running_loop()
    submitted: list[asyncio.Future[Any]] = []

    def mock_query(*args: Any, **kwargs: Any) -> Any:
        future = loop.create_future()
        if not submitted:
            future.cancel()
        submitted.append(future)
        return future

    resolver = MagicMock(spec=aiodns.DNSResolver)
    resolver.query.side_effect = mock_query
    resolver.nameservers = ["192.168.107.1"]

    with (
        patch.object(discovery, "DNS_RESPONSE_TIMEOUT", 10),
        patch.object(discovery, "QUERY_BUCKET_SIZE", 2),
    ):
        task = asyncio.create_task(
            discovery.async_query_for_ptrs(
                resolver,
                [
                    IPv4Address("192.168.107.2"),
                    IPv4Address("192.168.107.3"),
                ],
            )
        )
        for _ in range(5):
            await asyncio.sleep(0)
            if len(submitted) == 2:
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert resolver.cancel.call_count == 1
    assert submitted[0].cancelled()
    assert submitted[1].cancelled()


@pytest.mark.asyncio
async def test_async_get_hostnames_no_results(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_get_hostnames with no results."""
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
async def test_async_get_hostnames_silent_failure_is_blacklisted(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Cache a nameserver as failed when every PTR response was None."""
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
async def test_async_get_hostnames_all_silent_does_not_blacklist(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Leave the failed-nameserver cache empty when no nameserver succeeded."""
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
async def test_async_get_hostnames_all_responding(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_get_hostnames with responses for all IPs."""
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
async def test_async_get_hostnames_partial_responding(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_get_hostnames with responses for some IPs."""
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
async def test_async_get_hostnames_first_nameserver_fails(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_get_hostnames when the first nameserver fails."""
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
async def test_silent_nameserver_timeout_is_blacklisted(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Pin: a fully silent nameserver must land in _failed_nameservers."""
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
async def test_silent_nameserver_skipped_on_second_run(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Pin: a silently-failed nameserver must not be queried on the next run."""
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
        async with discovery.DiscoverHosts() as discover_hosts:
            net_data = SystemNetworkData(None, None)
            net_data.router_ip = IPv4Address("192.168.0.1")
            net_data.network = IPv4Network("192.168.0.0/31")
            net_data.nameservers = [
                IPv4Address("172.0.0.3"),
                IPv4Address("172.0.0.4"),
            ]
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
async def test_reload_on_resolv_conf_change(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_discover reloads system network data when resolv.conf changes."""
    net_data_1 = SystemNetworkData(None, None)
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = [IPv4Address("192.168.0.254")]

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.99")]

    setup_results = [net_data_1, net_data_2]

    async def fake_setup() -> SystemNetworkData:
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
async def test_no_reload_when_resolv_conf_unchanged(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_discover keeps cached data and failed cache when resolv.conf is stable."""
    net_data = SystemNetworkData(None, None)
    net_data.router_ip = IPv4Address("192.168.0.1")
    net_data.network = IPv4Network("192.168.0.0/24")
    net_data.nameservers = [IPv4Address("192.168.0.254")]

    call_count = 0

    async def fake_setup() -> SystemNetworkData:
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
async def test_reload_when_resolv_conf_appears(
    discover_hosts: discovery.DiscoverHosts,
) -> None:
    """Verify async_discover reloads when resolv.conf transitions from missing to present."""
    net_data_1 = SystemNetworkData(None, None)
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = []

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.254")]

    setup_results = [net_data_1, net_data_2]

    async def fake_setup() -> SystemNetworkData:
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
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    fake_ip_route = MagicMock()
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        async with discovery.DiscoverHosts() as discover_hosts:
            discover_hosts._sys_network_data = MagicMock(ip_route=fake_ip_route)

    fake_resolver.close.assert_awaited_once()
    fake_ip_route.close.assert_called_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_close_when_no_sys_network_data() -> None:
    """close() works even if async_discover was never called."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        async with discovery.DiscoverHosts() as discover_hosts:
            pass

    fake_resolver.close.assert_awaited_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_close_tolerates_ip_route_close_error() -> None:
    """A pyroute2 close exception does not propagate out of close()."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    fake_ip_route = MagicMock()
    fake_ip_route.close.side_effect = OSError("already closed")
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        async with discovery.DiscoverHosts() as discover_hosts:
            discover_hosts._sys_network_data = MagicMock(ip_route=fake_ip_route)

    fake_resolver.close.assert_awaited_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_async_context_manager_returns_self() -> None:
    """`async with DiscoverHosts()` yields the instance itself."""
    discover_hosts = discovery.DiscoverHosts()
    async with discover_hosts as ctx:
        assert ctx is discover_hosts


@pytest.mark.asyncio
async def test_close_with_real_resolver() -> None:
    """End-to-end: close() succeeds against the real aiodns DNSResolver."""
    async with discovery.DiscoverHosts():
        pass


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    """A second close() call is a no-op and does not re-await the resolver."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        discover_hosts = discovery.DiscoverHosts()

    await discover_hosts.close()
    await discover_hosts.close()

    fake_resolver.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_clears_ip_route_when_resolver_close_raises() -> None:
    """If resolver.close() raises, the pyroute2 socket is still released."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        discover_hosts = discovery.DiscoverHosts()

    fake_ip_route = MagicMock()
    fake_net_data = MagicMock(ip_route=fake_ip_route)
    discover_hosts._sys_network_data = fake_net_data

    with pytest.raises(RuntimeError, match="boom"):
        await discover_hosts.close()

    fake_ip_route.close.assert_called_once()
    assert discover_hosts._sys_network_data is None


@pytest.mark.asyncio
async def test_async_discover_after_close_raises() -> None:
    """async_discover() on a closed instance raises RuntimeError."""
    async with discovery.DiscoverHosts() as discover_hosts:
        pass
    with pytest.raises(RuntimeError, match="closed"):
        await discover_hosts.async_discover()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="pyroute2.iproute imports fcntl, which is not available on Windows",
)
@pytest.mark.asyncio
async def test_setup_failure_closes_ip_route() -> None:
    """_setup_sys_network_data closes AsyncIPRoute if async_setup() raises."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    fake_ip_route = MagicMock()

    import pyroute2

    with (
        patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver),
        patch.object(pyroute2, "AsyncIPRoute", return_value=fake_ip_route),
        patch(
            "aiodiscover.network.SystemNetworkData.async_setup",
            side_effect=RuntimeError("no local ip"),
        ),
    ):
        async with discovery.DiscoverHosts() as discover_hosts:
            with pytest.raises(RuntimeError, match="no local ip"):
                await discover_hosts._setup_sys_network_data()

    fake_ip_route.close.assert_called_once()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="pyroute2.iproute imports fcntl, which is not available on Windows",
)
@pytest.mark.asyncio
async def test_setup_failure_tolerates_close_error() -> None:
    """An OSError from AsyncIPRoute.close() during setup failure does not mask the original error."""
    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    fake_ip_route = MagicMock()
    fake_ip_route.close.side_effect = OSError("already closed")

    import pyroute2

    with (
        patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver),
        patch.object(pyroute2, "AsyncIPRoute", return_value=fake_ip_route),
        patch(
            "aiodiscover.network.SystemNetworkData.async_setup",
            side_effect=RuntimeError("no local ip"),
        ),
    ):
        async with discovery.DiscoverHosts() as discover_hosts:
            with pytest.raises(RuntimeError, match="no local ip"):
                await discover_hosts._setup_sys_network_data()

    fake_ip_route.close.assert_called_once()


@pytest.mark.asyncio
async def test_resolv_conf_reload_closes_old_ip_route() -> None:
    """When resolv.conf changes, the previous IPRoute socket is closed before reloading."""
    fake_ip_route_1 = MagicMock()
    fake_ip_route_2 = MagicMock()

    net_data_1 = SystemNetworkData(None, None)
    net_data_1.ip_route = fake_ip_route_1
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = [IPv4Address("192.168.0.254")]

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.ip_route = fake_ip_route_2
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.99")]

    setup_results = [net_data_1, net_data_2]

    async def fake_setup() -> SystemNetworkData:
        return setup_results.pop(0)

    signature_calls = iter([(1, 100), (2, 100)])

    def fake_sig() -> tuple[int, int]:
        return next(signature_calls)

    subnet_size = len(list(net_data_1.network.hosts()))

    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        discover_hosts = discovery.DiscoverHosts()

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
        fake_ip_route_1.close.assert_not_called()

        await discover_hosts.async_discover()
        assert discover_hosts._sys_network_data is net_data_2

    fake_ip_route_1.close.assert_called_once()
    fake_ip_route_2.close.assert_not_called()

    await discover_hosts.close()
    fake_ip_route_2.close.assert_called_once()


@pytest.mark.asyncio
async def test_resolv_conf_reload_tolerates_old_ip_route_close_error() -> None:
    """An OSError from the discarded IPRoute.close() does not break the reload."""
    fake_ip_route_1 = MagicMock()
    fake_ip_route_1.close.side_effect = OSError("already closed")
    fake_ip_route_2 = MagicMock()

    net_data_1 = SystemNetworkData(None, None)
    net_data_1.ip_route = fake_ip_route_1
    net_data_1.router_ip = IPv4Address("192.168.0.1")
    net_data_1.network = IPv4Network("192.168.0.0/24")
    net_data_1.nameservers = [IPv4Address("192.168.0.254")]

    net_data_2 = SystemNetworkData(None, None)
    net_data_2.ip_route = fake_ip_route_2
    net_data_2.router_ip = IPv4Address("192.168.0.1")
    net_data_2.network = IPv4Network("192.168.0.0/24")
    net_data_2.nameservers = [IPv4Address("192.168.0.99")]

    setup_results = [net_data_1, net_data_2]

    async def fake_setup() -> SystemNetworkData:
        return setup_results.pop(0)

    signature_calls = iter([(1, 100), (2, 100)])

    def fake_sig() -> tuple[int, int]:
        return next(signature_calls)

    subnet_size = len(list(net_data_1.network.hosts()))

    fake_resolver = MagicMock()
    fake_resolver.close = AsyncMock()
    with patch("aiodiscover.discovery.DNSResolver", return_value=fake_resolver):
        discover_hosts = discovery.DiscoverHosts()

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
        await discover_hosts.async_discover()

    assert discover_hosts._sys_network_data is net_data_2
    fake_ip_route_1.close.assert_called_once()

    await discover_hosts.close()
