from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_network
from pathlib import Path
from typing import TYPE_CHECKING, Any

import ifaddr
from cached_ipaddress import cached_ip_addresses
from ifaddr import Adapter

from .util import asyncio_timeout

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pyroute2.iproute import IPRoute
# Some MAC addresses will drop the leading zero so
# our mac validation must allow a single char
VALID_MAC_ADDRESS = re.compile("^([0-9A-Fa-f]{1,2}[:-]){5}([0-9A-Fa-f]{1,2})$")

ARP_CACHE_POPULATE_TIME = 10
ARP_TIMEOUT = 10

DEFAULT_NETWORK_PREFIX = 24


PRIVATE_AND_LOCAL_NETWORKS = (
    ip_network("127.0.0.0/8"),
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
)

DEFAULT_TARGET = "10.255.255.255"
MDNS_TARGET_IP = "224.0.0.251"
PUBLIC_TARGET_IP = "8.8.8.8"
LOOPBACK_TARGET_IP = "127.0.0.1"

IGNORE_MACS = {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"}

# pyroute2 reports neighbour attributes under different names depending on
# the platform stub: Linux netlink uses NDA_*, the macOS stub added in
# pyroute2 0.9.2 uses NEIGH_*. Accept both so the netlink path works on
# either OS without falling all the way through to the arp -an reader.
NEIGHBOUR_IP_KEYS = frozenset({"NDA_DST", "NEIGH_IP"})
NEIGHBOUR_LLADDR_KEYS = frozenset({"NDA_LLADDR", "NEIGH_LLADDR"})

RESOLV_CONF_PATH = "/etc/resolv.conf"


@dataclass(frozen=True, slots=True)
class ResolvConfSignature:
    """Signature describing resolv.conf at a point in time."""

    mtime_ns: int
    size: int


def load_resolv_conf_with_signature() -> tuple[
    ResolvConfSignature, list[IPv4Address | IPv6Address]
]:
    """Load resolv.conf and return (signature, nameservers) from the same fd."""
    with Path(RESOLV_CONF_PATH).open() as file:
        stat = os.fstat(file.fileno())
        lines = tuple(file)
    return ResolvConfSignature(stat.st_mtime_ns, stat.st_size), parse_resolv_conf(lines)


def resolv_conf_signature() -> ResolvConfSignature | None:
    """Return a signature describing the current resolv.conf, or None if missing."""
    try:
        stat = Path(RESOLV_CONF_PATH).stat()
    except OSError:
        return None
    return ResolvConfSignature(stat.st_mtime_ns, stat.st_size)


def parse_resolv_conf(lines: Iterable[str]) -> list[IPv4Address | IPv6Address]:
    """Parse the resolv.conf."""
    nameservers: list[IPv4Address | IPv6Address] = []
    for line in lines:
        line = line.strip()
        if not len(line):
            continue
        if line[0] in ("#", ";"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts
        if (
            key == "nameserver"
            and (ip_addr := cached_ip_addresses(value))
            and ip_addr not in nameservers
        ):
            nameservers.append(ip_addr)
    return nameservers


def _parse_ipv4(value: str) -> IPv4Address | None:
    """Parse a string into an IPv4Address, dropping IPv6 / invalid input."""
    ip_addr = cached_ip_addresses(value)
    return ip_addr if isinstance(ip_addr, IPv4Address) else None


def get_local_ip(target: str = DEFAULT_TARGET) -> IPv4Address | None:
    """Find the local ip address."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setblocking(False)
    try:
        s.connect((target, 1))
        source = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return _parse_ipv4(source)


def get_network(local_ip: IPv4Address, adapters: list[Adapter]) -> IPv4Network:
    """Search adapters for the network and broadcast ip."""
    network_prefix = (
        get_ip_prefix_from_adapters(str(local_ip), adapters) or DEFAULT_NETWORK_PREFIX
    )
    network = ip_network(f"{local_ip}/{network_prefix}", False)
    if TYPE_CHECKING:
        assert isinstance(network, IPv4Network)
    return network


def get_ip_prefix_from_adapters(local_ip: str, adapters: list[Adapter]) -> int | None:
    """Find the network prefix for an adapter."""
    for adapter in adapters:
        for ip in adapter.ips:
            if local_ip == ip.ip:
                return ip.network_prefix
    return None


def get_attrs_key(data: Any, key: Any) -> str | None:
    """Lookup an attrs key in pyroute2 data."""
    for attr_key, attr_value in data["attrs"]:
        if attr_key == key:
            return attr_value
    return None


def get_router_ip(ipr: IPRoute) -> IPv4Address | None:
    """Obtain the router ip from the default route."""
    gateway = get_attrs_key(ipr.get_default_routes()[0], "RTA_GATEWAY")
    return _parse_ipv4(gateway) if gateway else None


def _get_macos_default_gateway(family: str = "inet") -> str | None:
    """
    Get the default gateway IP on macOS via `route -n get default`.

    family: "inet" for IPv4, "inet6" for IPv6.
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["route", "-n", "get", f"-{family}", "default"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            close_fds=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("gateway:"):
            # IPv6 gateways may include a zone suffix (e.g. fe80::1%en0)
            return line.split(":", 1)[1].strip().split("%", 1)[0] or None
    return None


def _fill_neighbor(neighbours: dict[str, str], ip: str, mac: str) -> None:
    """Add a neighbor if it is valid."""
    if not (ip_addr := cached_ip_addresses(ip)):
        return
    if (
        ip_addr.is_loopback
        or ip_addr.is_link_local
        or ip_addr.is_multicast
        or ip_addr.is_unspecified
    ):
        return
    if not VALID_MAC_ADDRESS.match(mac):
        return
    mac = ":".join([i.zfill(2) for i in mac.split(":")])
    if mac in IGNORE_MACS:
        return
    neighbours[ip] = mac


def async_populate_arp(ip_addresses: Iterable[str]) -> socket.socket:
    """Send an empty packet to a host to populate the arp cache."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
    sock.setblocking(False)
    for ip_addr in ip_addresses:
        with suppress(Exception):
            sock.sendto(b"", (ip_addr, 80))
    return sock


class SystemNetworkData:
    """Gather system network data."""

    network: IPv4Network
    adapters: list[Adapter]
    nameservers: list[IPv4Address | IPv6Address]
    router_ip: IPv4Address | None = None
    local_ip: IPv4Address | None = None
    resolv_conf_signature: ResolvConfSignature | None = None

    def __init__(self, ip_route: IPRoute | None, local_ip: str | None = None) -> None:
        """Init system network data."""
        self.ip_route = ip_route
        self.local_ip = _parse_ipv4(local_ip) if local_ip else None

    def setup(self) -> None:
        """Obtain the local network data."""
        # Default to an empty list so attribute access stays safe when
        # resolv.conf is absent on Windows (the FileNotFoundError below is
        # swallowed there) and so later code can iterate unconditionally.
        self.nameservers = []
        try:
            signature, resolvers = load_resolv_conf_with_signature()
        except FileNotFoundError:
            if sys.platform != "win32":
                raise
        else:
            self.resolv_conf_signature = signature
            self.nameservers = [
                ip_addr
                for ip_addr in resolvers
                if any(ip_addr in network for network in PRIVATE_AND_LOCAL_NETWORKS)
            ]
        self.adapters = list(ifaddr.get_adapters())
        if not self.local_ip:
            self.local_ip = (
                get_local_ip(DEFAULT_TARGET)
                or get_local_ip(MDNS_TARGET_IP)
                or get_local_ip(PUBLIC_TARGET_IP)
                or get_local_ip(LOOPBACK_TARGET_IP)
            )
        assert self.local_ip is not None
        self.network = get_network(self.local_ip, self.adapters)
        if self.ip_route:
            with suppress(Exception):
                self.router_ip = get_router_ip(self.ip_route)
        if not self.router_ip and sys.platform == "darwin":
            # pyroute2 is Linux-only; on macOS parse `route -n get default`
            gateway = _get_macos_default_gateway()
            if gateway:
                self.router_ip = _parse_ipv4(gateway)
        if not self.router_ip:
            # First usable host in the network — `network_address + 1` for
            # any IPv4 prefix /30 or shorter, matching the conventional
            # router placement. The previous string-slice heuristic
            # (`network_address[:-1] + "1"`) only happens to produce the
            # right address when the network address ends in `0`, so it
            # silently mis-pointed the fallback for /25, /26, /27, /28,
            # /29, /30 networks whose base is `.64`/`.128`/`.192`/etc.
            self.router_ip = next(self.network.hosts(), None)

    async def async_get_neighbours(self, ips: Iterable[str]) -> dict[str, str]:
        """Get neighbours with best available method."""
        neighbours = await self._async_get_neighbours()
        ips_missing_arp = [ip for ip in ips if ip not in neighbours]
        if not ips_missing_arp:
            return neighbours
        sock = async_populate_arp(ips_missing_arp)
        try:
            await asyncio.sleep(ARP_CACHE_POPULATE_TIME)
        finally:
            sock.close()
        neighbours.update(await self._async_get_neighbours())
        return neighbours

    async def _async_get_neighbours(self) -> dict[str, str]:
        """Get neighbours from the arp table."""
        if self.ip_route:
            return await self._async_get_neighbours_ip_route()
        return await self._async_get_neighbours_arp()

    async def _async_get_neighbours_arp(self) -> dict[str, str]:
        """Get neighbours with arp command."""
        neighbours: dict[str, str] = {}
        try:
            arp = await asyncio.create_subprocess_exec(
                "arp",
                "-a",
                "-n",
                stdin=None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                close_fds=False,
            )
        except OSError:
            return neighbours
        try:
            async with asyncio_timeout(ARP_TIMEOUT):
                out_data, _ = await arp.communicate()
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            with suppress(ProcessLookupError):
                arp.kill()
            with suppress(OSError):
                await arp.wait()
            if isinstance(exc, asyncio.CancelledError):
                raise
            return neighbours

        for line in out_data.decode().splitlines():
            chomped = line.strip()
            data = chomped.split()
            if len(data) < 4:
                continue
            ip = data[1].strip("()")
            mac = data[3]
            _fill_neighbor(neighbours, ip, mac)

        return neighbours

    async def _async_get_neighbours_ip_route(self) -> dict[str, str]:
        """Get neighbours with pyroute2."""
        neighbours: dict[str, str] = {}
        loop = asyncio.get_running_loop()
        # This shouldn't ever block but it does
        # interact with netlink so its safer to run
        # in the executor
        if TYPE_CHECKING:
            assert self.ip_route is not None
        for neighbour in await loop.run_in_executor(None, self.ip_route.get_neighbours):
            ip = None
            mac = None
            for key, value in neighbour["attrs"]:
                if key in NEIGHBOUR_IP_KEYS:
                    ip = value
                elif key in NEIGHBOUR_LLADDR_KEYS:
                    mac = value
            if ip and mac:
                _fill_neighbor(neighbours, ip, mac)

        return neighbours
