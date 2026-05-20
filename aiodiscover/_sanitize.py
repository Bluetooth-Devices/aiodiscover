from __future__ import annotations

# Per-column caps for peer-supplied labels displayed by the CLI. These match
# the maximum sane widths for the values we actually print:
#   - hostname: RFC 1035 LDH label = 63 chars
#   - mac: "xx:xx:xx:xx:xx:xx" = 17 chars
#   - ip: IPv4 "xxx.xxx.xxx.xxx" = 15 chars; IPv6 expanded = 39 chars
# A hostile DHCP/DNS/ARP responder upstream of us is already filtered by the
# validators in discovery.py / network.py, but capping again here is cheap
# defense in depth for the terminal-output path.
MAX_HOSTNAME_LEN = 63
MAX_MAC_LEN = 17
MAX_IP_LEN = 45


def safe_label_str(raw: str, limit: int) -> str:
    """Strip non-printables and length-cap a peer-supplied label for output."""
    return "".join(filter(str.isprintable, raw))[:limit]
