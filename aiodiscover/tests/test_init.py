#!/usr/bin/env python
from __future__ import annotations

import aiodiscover


def test_get_module_version() -> None:
    """Verify get_module_version does not throw."""
    assert aiodiscover.get_module_version() == aiodiscover.__version__
