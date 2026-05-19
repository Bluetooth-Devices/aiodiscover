from __future__ import annotations

import asyncio
import logging
import re
from contextlib import suppress
from functools import lru_cache, partial
from itertools import islice
from typing import TYPE_CHECKING, Any, cast

import pycares
from aiodns import DNSResolver

from .network import SystemNetworkData, resolv_conf_signature

if TYPE_CHECKING:
    from collections.abc import Iterable
    from ipaddress import IPv4Address, IPv6Address
    from types import TracebackType

    from pyroute2.iproute import IPRoute

    from .network import ResolvConfSignature

HOSTNAME = "hostname"
MAC_ADDRESS = "macaddress"
IP_ADDRESS = "ip"
MAX_ADDRESSES = 2048
QUERY_BUCKET_SIZE = 64

DNS_RESPONSE_TIMEOUT = 2

# 24 hours
CACHE_CLEAR_INTERVAL = 60 * 60 * 24


_LOGGER = logging.getLogger(__name__)

# RFC 1035 preferred-name LDH label: letters, digits, hyphens; up to 63 chars,
# must start and end with an alphanumeric. PTR responses are returned from
# whichever nameserver answered — including untrusted ones on the local
# segment — so any non-conforming label is dropped rather than propagated to
# downstream consumers (e.g. Home Assistant's dhcp integration).
_VALID_HOSTNAME_LABEL = re.compile(
    r"^(?=.{1,63}$)[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?$"
)


@lru_cache(maxsize=MAX_ADDRESSES)
def decode_idna(name: str) -> str:
    """Decode an idna name."""
    try:
        return name.encode().decode("idna")
    except UnicodeError:
        return name


def dns_message_short_hostname(dns_message: Any | None) -> str | None:
    """Get the short hostname from a dns message."""
    if dns_message is None:
        return None
    name: str = dns_message.name
    short = name.partition(".")[0]
    # Validate the on-the-wire label against RFC 1035 LDH before any
    # IDNA decoding. Legitimate punycode labels (xn--*) are themselves
    # LDH-clean, so this still admits internationalised hostnames.
    if not _VALID_HOSTNAME_LABEL.match(short):
        return None
    if short.startswith("xn--"):
        short = decode_idna(short)
    return short


async def async_query_for_ptrs(
    resolver: DNSResolver,
    ips_to_lookup: list[IPv4Address],
) -> list[Any | None]:
    """Fetch PTR records for a list of ips."""
    results: list[Any | None] = []
    for ip_chunk in chunked(ips_to_lookup, QUERY_BUCKET_SIZE):
        if TYPE_CHECKING:
            ip_chunk = cast("list[IPv4Address]", ip_chunk)
        futures = [resolver.query(ip.reverse_pointer, "PTR") for ip in ip_chunk]
        # Belt-and-braces outer timeout: aiodns/pycares honour the per-query
        # `DNS_RESPONSE_TIMEOUT` configured at resolver construction, but a
        # silent UDP black-hole or a future regression in the resolver could
        # leave futures pending forever and wedge the discovery loop. Cancel
        # anything still pending after the budget and treat it as failed.
        _, pending = await asyncio.wait(futures, timeout=DNS_RESPONSE_TIMEOUT + 1)
        for future in pending:
            future.cancel()
        results.extend(
            None if (future in pending or future.exception()) else future.result()
            for future in futures
        )
    resolver.cancel()
    return results


def take(take_num: int, iterable: Iterable[Any]) -> list[Any]:
    """
    Return first n items of the iterable as a list.

    From itertools recipes
    """
    return list(islice(iterable, take_num))


def chunked(iterable: Iterable[Any], chunked_num: int) -> Iterable[Any]:
    """
    Break *iterable* into lists of length *n*.

    From more-itertools
    """
    return iter(partial(take, chunked_num, iter(iterable)), [])


class DiscoverHosts:
    """Discover hosts on the network by ARP and PTR lookup."""

    def __init__(self, no_recurse: bool = True) -> None:
        """
        Init the discovery hosts.

        Args:
            no_recurse: If True (default), DNS queries will not request recursion.
                       This prevents routers from forwarding PTR queries to external
                       DNS servers, avoiding potential IP bans from public DNS services.

        """
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._sys_network_data: SystemNetworkData | None = None
        self._resolv_conf_signature: ResolvConfSignature | None = None
        self._failed_nameservers: set[IPv4Address | IPv6Address] = set()
        self._last_cache_clear = loop.time()
        self._closed = False

        # Create resolver with optional no_recurse flag
        resolver_kwargs = {"timeout": DNS_RESPONSE_TIMEOUT}
        if no_recurse:
            resolver_kwargs["flags"] = pycares.ARES_FLAG_NORECURSE
        self._resolver = DNSResolver(**resolver_kwargs)

    async def __aenter__(self) -> DiscoverHosts:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """
        Release the underlying DNS resolver and pyroute2 socket.

        After close() the instance must not be reused; calling close()
        again is a no-op.
        """
        if self._closed:
            return
        self._closed = True
        await self._resolver.close()
        if self._sys_network_data is not None:
            ip_route = self._sys_network_data.ip_route
            if ip_route is not None:
                with suppress(OSError):
                    ip_route.close()
            self._sys_network_data = None

    def _setup_sys_network_data(self) -> SystemNetworkData:
        ip_route: IPRoute | None = None
        with suppress(Exception):
            from pyroute2.iproute import IPRoute

            ip_route = IPRoute()
        sys_network_data = SystemNetworkData(ip_route)
        sys_network_data.setup()
        return sys_network_data

    def _cleanup_cache(self) -> None:
        """
        Clear the cache of failed nameservers.

        Just because a nameserver failed once doesn't mean it will fail again
        as it may have been a transient issue. Our goal is to avoid spamming
        the same nameservers over and over if they are unresponsive, but not
        to permanently skip them since they may become responsive again.
        """
        now = self._loop.time()
        if now - self._last_cache_clear > CACHE_CLEAR_INTERVAL:
            self._failed_nameservers.clear()
            self._last_cache_clear = now

    async def async_discover(self) -> list[dict[str, str]]:
        """Discover hosts on the network by ARP and PTR lookup."""
        current_signature = await self._loop.run_in_executor(
            None, resolv_conf_signature
        )
        if (
            self._sys_network_data is not None
            and current_signature != self._resolv_conf_signature
        ):
            _LOGGER.debug(
                "resolv.conf changed; reloading network data and "
                "clearing failed nameservers cache",
            )
            self._sys_network_data = None
            self._failed_nameservers.clear()

        if not self._sys_network_data:
            self._sys_network_data = await self._loop.run_in_executor(
                None,
                self._setup_sys_network_data,
            )
            # Cache the in-fd signature — the upfront stat can disagree if
            # a symlink target was swapped in between. Fall back to the
            # upfront stat when setup didn't populate one.
            self._resolv_conf_signature = (
                self._sys_network_data.resolv_conf_signature or current_signature
            )
        sys_network_data = self._sys_network_data
        network = sys_network_data.network
        if network.num_addresses > MAX_ADDRESSES:
            _LOGGER.debug(
                "The network %s exceeds the maximum number of addresses, %s; No scanning performed",
                network,
                MAX_ADDRESSES,
            )
            return []
        self._cleanup_cache()
        hostnames = await self.async_get_hostnames(sys_network_data)
        neighbours = await sys_network_data.async_get_neighbours(hostnames.keys())
        return [
            {
                HOSTNAME: hostname,
                MAC_ADDRESS: neighbours[ip],
                IP_ADDRESS: ip,
            }
            for ip, hostname in hostnames.items()
            if ip in neighbours
        ]

    async def _async_get_nameservers(
        self,
        net_data: SystemNetworkData,
    ) -> list[IPv4Address | IPv6Address]:
        """Get nameservers to query."""
        if (
            # If the Router IP is known
            (router_ip := net_data.router_ip)
            # And the router IP is not already a nameserver
            and router_ip not in net_data.nameservers
            # If there are no in-network nameservers
            and not any(ip in net_data.network for ip in net_data.nameservers)
            # And the router responds to ARP
            and str(router_ip) in await net_data.async_get_neighbours([str(router_ip)])
        ):
            return [*net_data.nameservers, router_ip]
        return net_data.nameservers

    async def async_get_hostnames(
        self,
        sys_network_data: SystemNetworkData,
    ) -> dict[str, str]:
        """Lookup PTR records for all addresses in the network."""
        all_nameservers = await self._async_get_nameservers(sys_network_data)
        _LOGGER.debug("Using nameservers %s", all_nameservers)
        _LOGGER.debug("Using network %s", sys_network_data.network)
        _LOGGER.debug("Previous failed nameservers %s", self._failed_nameservers)
        ips = list(sys_network_data.network.hosts())
        hostnames: dict[str, str] = {}
        failed_nameservers_this_run: set[IPv4Address | IPv6Address] = set()
        for nameserver in all_nameservers:
            if nameserver in self._failed_nameservers:
                _LOGGER.debug("Skipping previously failed nameserver %s", nameserver)
                continue
            ips_to_lookup = [ip for ip in ips if str(ip) not in hostnames]
            self._resolver.nameservers = [str(nameserver)]
            results = await async_query_for_ptrs(self._resolver, ips_to_lookup)
            added_any = False
            for idx, ip in enumerate(ips_to_lookup):
                short_host = dns_message_short_hostname(results[idx])
                if short_host is None:
                    continue
                hostnames[str(ip)] = short_host
                added_any = True
            if not added_any:
                _LOGGER.debug("No usable PTRs from %s", nameserver)
                failed_nameservers_this_run.add(nameserver)
                continue
            if hostnames:
                # As soon as we have a responsive nameserver, there
                # is no need to query additional fallbacks
                break
        _LOGGER.debug("Failed nameservers this run %s", failed_nameservers_this_run)
        if hostnames:
            # If we have any working nameservers, keep track of which
            # ones failed this run so we don't try them again
            self._failed_nameservers.update(failed_nameservers_this_run)
        return hostnames
