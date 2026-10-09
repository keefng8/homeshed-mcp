# Network — Port Check

## ID
`network.port_check`

## Purpose
Test TCP connectivity to a `host:port` with a real connection attempt: is this service reachable from
the server, and how fast does it answer?

## Which hosts it will check
Public hosts only by default. Every address the name resolves to must be publicly routable, and the tool
connects to the vetted address itself, so a DNS answer can't switch it to a private one halfway through.
Private, loopback, link-local and cloud-metadata addresses, and names such as `localhost` or `*.lan`, are
refused. To check hosts on your own network, allow your ranges explicitly, comma-separated:
`NETWORK_ALLOWED_CIDRS=192.168.0.0/16`.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `host` | string | — | Required. Hostname or IP to connect to. |
| `port` | integer | — | Required. TCP port, 1-65535. |
| `timeout` | number | `3.0` | Seconds to wait for the connection, 0.1-30. |

## Returns
`{host, port, open: bool, latency_ms: float | None, error: str | None}` — `latency_ms` is the
TCP handshake time in milliseconds, `null` if the connection failed. A **closed or unreachable
port is a normal, successful result** (`open: false` with an `error` reason), not a raised
exception — that's the answer being asked for.

## Not supported
UDP (TCP only: UDP has no handshake to test in a generic way). ICMP ping (raw ICMP sockets need
elevated privileges).

## Errors
Raises `PortCheckError` for a malformed request (empty host, port/timeout out of range) or a host that
isn't allowed (see above) — never for a real network outcome, which is always a normal return value.

## Implementation
`tools/network/port_check.py`, guard in `netguard.py`. Standard library `socket` only.

## Machine-readable definition
`port-check.json`, same directory.

## Related
- `network.dns.lookup`
