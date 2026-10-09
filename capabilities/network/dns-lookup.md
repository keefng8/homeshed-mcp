# Network — DNS Lookup

## ID
`network.dns.lookup`

## Purpose
Resolve a hostname to its IP address(es) — A/AAAA records only. Deterministic (per
`.claude/local-ai-operating-rules.md` §2) — exact answer from the OS resolver, not an LLM guess.

## Parameters
| Name | Type | Default | Description |
|---|---|---|---|
| `hostname` | string | — | Required. Hostname to resolve. |

## Returns
`{hostname, addresses: [{ip, family}]}` — `family` is `"IPv4"` or `"IPv6"`, deduplicated.

## Not supported
MX, TXT, CNAME, or any record type beyond A/AAAA. Uses Python's stdlib `socket.getaddrinfo`
only — no `dnspython` dependency for what's currently just "can this hostname be resolved."
Extend with a real DNS library if MX/TXT lookups become a real need, not speculatively.

## Errors
Raises with a clear message if the hostname doesn't resolve or the input is empty.

## Implementation
`mcp-server/tools/network/dns_lookup.py`.

## Machine-readable definition
`dns-lookup.json`, same directory.

## Related
- `network.port_check`
