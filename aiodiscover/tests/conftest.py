#!/usr/bin/env python
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from aiodiscover import discovery


@pytest_asyncio.fixture
async def discover_hosts() -> AsyncIterator[discovery.DiscoverHosts]:
    """Yield a DiscoverHosts and release its resolver + pyroute2 socket on teardown."""
    instance = discovery.DiscoverHosts()
    try:
        yield instance
    finally:
        await instance.close()
