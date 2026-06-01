from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
import pytest_asyncio

from aiodiscover import discovery

BlockBuster: Any = None
blockbuster_ctx: Any = None

with contextlib.suppress(ImportError):
    import blockbuster as _bb

    BlockBuster = _bb.BlockBuster
    blockbuster_ctx = _bb.blockbuster_ctx


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
