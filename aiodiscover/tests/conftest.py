from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio

from aiodiscover import discovery

try:
    from blockbuster import BlockBuster, blockbuster_ctx
except ImportError:
    BlockBuster = None  # type: ignore[assignment,misc]
    blockbuster_ctx = None  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def blockbuster() -> Iterator[BlockBuster | None]:
    """Fail any test that performs a blocking call inside the asyncio loop."""
    if blockbuster_ctx is None:
        yield None
        return
    with blockbuster_ctx() as bb:
        yield bb


@pytest_asyncio.fixture
async def discover_hosts() -> AsyncIterator[discovery.DiscoverHosts]:
    """Yield a DiscoverHosts and release its resolver + pyroute2 socket on teardown."""
    instance = discovery.DiscoverHosts()
    try:
        yield instance
    finally:
        await instance.close()
