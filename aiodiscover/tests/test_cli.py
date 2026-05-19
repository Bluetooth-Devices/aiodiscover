#!/usr/bin/env python
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aiodiscover
from aiodiscover import cli

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


SAMPLE_HOSTS = [
    {"hostname": "router", "ip": "192.168.1.1", "macaddress": "aa:bb:cc:dd:ee:ff"},
    {"hostname": "laptop", "ip": "192.168.1.2", "macaddress": "11:22:33:44:55:66"},
]


def _patch_discover(hosts: list[dict[str, str]] | None = None) -> MagicMock:
    instance = MagicMock()
    instance.async_discover = AsyncMock(return_value=hosts or SAMPLE_HOSTS)
    instance.close = AsyncMock(return_value=None)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=instance)
    return patch("aiodiscover.cli.DiscoverHosts", factory)


def test_build_parser_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args([])
    assert args.json is False
    assert args.no_recurse is True
    assert args.log_level == "WARNING"
    assert args.indent == 2


def test_build_parser_json_flag() -> None:
    args = cli.build_parser().parse_args(["--json", "--indent", "4"])
    assert args.json is True
    assert args.indent == 4


def test_build_parser_recurse_overrides_default() -> None:
    args = cli.build_parser().parse_args(["--recurse"])
    assert args.no_recurse is False


def test_build_parser_verbose_and_debug_shortcuts() -> None:
    assert cli.build_parser().parse_args(["-v"]).log_level == "INFO"
    assert cli.build_parser().parse_args(["--debug"]).log_level == "DEBUG"


def test_build_parser_recurse_mutually_exclusive() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--no-recurse", "--recurse"])


def test_main_pprint_output(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        exit_code = cli.main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "router" in captured.out
    assert "192.168.1.1" in captured.out


def test_main_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        exit_code = cli.main(["--json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    parsed = json.loads(captured.out)
    assert parsed == SAMPLE_HOSTS


def test_main_json_custom_indent(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        cli.main(["--json", "--indent", "0"])
    captured = capsys.readouterr()
    # indent=0 still emits newlines between elements; sort_keys is stable.
    assert json.loads(captured.out) == SAMPLE_HOSTS


def test_main_passes_no_recurse_default() -> None:
    with patch("aiodiscover.cli.DiscoverHosts") as mocked:
        instance = mocked.return_value
        instance.async_discover = AsyncMock(return_value=[])
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        cli.main([])
        mocked.assert_called_once_with(no_recurse=True)


def test_main_passes_recurse_flag() -> None:
    with patch("aiodiscover.cli.DiscoverHosts") as mocked:
        instance = mocked.return_value
        instance.async_discover = AsyncMock(return_value=[])
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        cli.main(["--recurse"])
        mocked.assert_called_once_with(no_recurse=False)


def test_main_keyboard_interrupt_returns_130() -> None:
    def _raise(coro: object) -> object:
        coro.close()  # type: ignore[attr-defined]
        raise KeyboardInterrupt

    with patch("aiodiscover.cli.asyncio.run", side_effect=_raise):
        assert cli.main([]) == 130


def test_main_version_flag_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert aiodiscover.__version__ in captured.out


def test_main_module_entry_point() -> None:
    """Ensure ``python -m aiodiscover`` wires through to cli.main."""
    with (
        patch("aiodiscover.cli.main", return_value=0) as mocked_main,
        patch.object(sys, "argv", ["aiodiscover"]),
    ):
        import runpy

        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("aiodiscover", run_name="__main__")
        assert excinfo.value.code == 0
        mocked_main.assert_called_once()
