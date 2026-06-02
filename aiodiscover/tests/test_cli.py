from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AbstractContextManager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aiodiscover
from aiodiscover import cli
from aiodiscover._sanitize import MAX_HOSTNAME_LEN, safe_label_str

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


SAMPLE_HOSTS = [
    {"hostname": "router", "ip": "192.168.1.1", "macaddress": "aa:bb:cc:dd:ee:ff"},
    {"hostname": "laptop", "ip": "192.168.1.2", "macaddress": "11:22:33:44:55:66"},
]


def _patch_discover(
    hosts: list[dict[str, str]] | None = None,
) -> AbstractContextManager[MagicMock]:
    instance = MagicMock()
    instance.async_discover = AsyncMock(
        return_value=SAMPLE_HOSTS if hosts is None else hosts
    )
    instance.close = AsyncMock(return_value=None)
    instance.__aenter__ = AsyncMock(return_value=instance)
    instance.__aexit__ = AsyncMock(return_value=None)
    factory = MagicMock(return_value=instance)
    return patch("aiodiscover.cli.DiscoverHosts", factory)


def test_build_parser_defaults() -> None:
    parser = cli.build_parser()
    args = parser.parse_args([])
    assert args.format == "table"
    assert args.no_recurse is True
    assert args.log_level == "WARNING"
    assert args.indent == 2


def test_build_parser_json_flag() -> None:
    args = cli.build_parser().parse_args(["--json", "--indent", "4"])
    assert args.format == "json"
    assert args.indent == 4


def test_build_parser_format_flag() -> None:
    assert cli.build_parser().parse_args(["--format", "pprint"]).format == "pprint"
    assert cli.build_parser().parse_args(["-f", "json"]).format == "json"


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


def test_main_table_output_default(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        exit_code = cli.main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    lines = captured.out.splitlines()
    assert lines[0].split() == ["Hostname", "IP", "MAC"]
    assert lines[2].startswith("router")
    assert lines[3].startswith("laptop")


def test_main_table_sorts_by_ip_numerically(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hosts = [
        {"hostname": "a", "ip": "192.168.1.10", "macaddress": "aa:bb:cc:dd:ee:01"},
        {"hostname": "b", "ip": "192.168.1.2", "macaddress": "aa:bb:cc:dd:ee:02"},
    ]
    with _patch_discover(hosts):
        cli.main([])
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("b")
    assert out[3].startswith("a")


def test_main_table_sorts_malformed_ip_last(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hosts = [
        {"hostname": "bogus", "ip": "not-an-ip", "macaddress": "aa:bb:cc:dd:ee:01"},
        {"hostname": "v4host", "ip": "10.0.0.1", "macaddress": "aa:bb:cc:dd:ee:02"},
        {"hostname": "v6host", "ip": "fe80::1", "macaddress": "aa:bb:cc:dd:ee:03"},
    ]
    with _patch_discover(hosts):
        cli.main([])
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("v4host")
    assert out[3].startswith("v6host")
    assert out[4].startswith("bogus")


def test_main_table_empty(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover([]):
        cli.main([])
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["Hostname", "IP", "MAC"]
    assert len(out) == 2


def test_main_pprint_output(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        exit_code = cli.main(["--format", "pprint"])
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
    assert [h["hostname"] for h in parsed] == ["router", "laptop"]


def test_main_json_custom_indent(capsys: pytest.CaptureFixture[str]) -> None:
    with _patch_discover():
        cli.main(["--json", "--indent", "0"])
    captured = capsys.readouterr()
    assert [h["hostname"] for h in json.loads(captured.out)] == ["router", "laptop"]


def test_main_sanitizes_malicious_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hostile = [
        {
            "hostname": "evil\x1b[2Jhost",
            "ip": "192.168.1.5\n",
            "macaddress": "aa:bb:cc\x00:dd:ee:ff",
        }
    ]
    with _patch_discover(hostile):
        cli.main(["--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed[0]["hostname"] == "evil[2Jhost"
    assert parsed[0]["ip"] == "192.168.1.5"
    assert parsed[0]["macaddress"] == "aa:bb:cc:dd:ee:ff"


def test_main_passes_no_recurse_default() -> None:
    with patch("aiodiscover.cli.DiscoverHosts") as mocked:
        instance = mocked.return_value
        instance.async_discover = AsyncMock(return_value=[])
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        cli.main([])
        mocked.assert_called_once_with(no_recurse=True, local_ip=None)


def test_main_passes_recurse_flag() -> None:
    with patch("aiodiscover.cli.DiscoverHosts") as mocked:
        instance = mocked.return_value
        instance.async_discover = AsyncMock(return_value=[])
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        cli.main(["--recurse"])
        mocked.assert_called_once_with(no_recurse=False, local_ip=None)


def test_main_passes_local_ip_flag() -> None:
    """--local-ip is forwarded through to DiscoverHosts."""
    with patch("aiodiscover.cli.DiscoverHosts") as mocked:
        instance = mocked.return_value
        instance.async_discover = AsyncMock(return_value=[])
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=None)
        cli.main(["--local-ip", "192.168.5.10"])
        mocked.assert_called_once_with(no_recurse=True, local_ip="192.168.5.10")


def test_build_parser_local_ip_default_is_none() -> None:
    """Omitting --local-ip leaves the value at None."""
    args = cli.build_parser().parse_args([])
    assert args.local_ip is None


@pytest.mark.parametrize(
    "bad_value",
    ["not-an-ip", "999.999.999.999", "192.168.1", "::1", "2001:db8::1"],
)
def test_build_parser_local_ip_rejects_invalid(
    bad_value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """--local-ip validates at parse time so users see argparse's friendly error."""
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--local-ip", bad_value])
    err = capsys.readouterr().err
    assert "IPv4" in err


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


def test_safe_label_str_strips_non_printable() -> None:
    assert safe_label_str("a\x1b[31mb\x00c\nd", 100) == "a[31mbcd"


def test_safe_label_str_length_caps() -> None:
    assert safe_label_str("x" * 200, 10) == "x" * 10


def test_safe_label_str_unicode_printable_survives() -> None:
    assert safe_label_str("café", MAX_HOSTNAME_LEN) == "café"
