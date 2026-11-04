# Refactoring plan for the vendored `lib/*` libraries

**Scope:** the vendored upstream libraries (`lib/connect`, `lib/sdk`,
`lib/proxy`, `lib/userwireguard`, `lib/glog`) — **not** the client code in
`cmd/`, `gui/`, `scripts/` (that work is described in
[`docs/analysis.md`](analysis.md) §8 and §8.11).
**Origin:** snapshot of 2026-06-06, committed in `8be72d7`.
**Status as of 2026-11-04:** 5 tickets closed, 1 partial, 6 open. Statuses below
were re-verified against the code and by running the tests on 2026-11-04.

Legend: **DONE** — verified in the tree; **PARTIAL** — mitigated but the ticket
is not finished; **OPEN** — not started.

## Phase 0 — test baseline and bind behaviour (all DONE)

### 0.1 `lib/userwireguard` bind interface drift — **DONE**

Stale `Bind.Open` test adapters were updated; `go test ./...` in
`lib/userwireguard` passes today (conn 0.005 s, device 1.194 s).

### 0.2 IPv4-only bind when IPv6 is unavailable — **DONE**

`lib/userwireguard/conn/bind_std.go:122` falls back on
`EAFNOSUPPORT`/`EADDRNOTAVAIL` instead of failing.

### 0.3 Extender tests on ephemeral ports — **DONE**

`lib/connect/extender/extender_test.go:55,62` reserves and waits for ephemeral
TCP ports.

### 0.4 `glog` default file sink — **DONE**

`lib/glog/glog_file.go:55` falls back to `os.TempDir()` when the log directory
is unwritable, and `:188` registers the file sink from `init`; `lib/glog` tests
pass.

## Phase 1 — JWT

### 1.1 `network_id` claim — **DONE**

`lib/connect/jwt.go:33` reads `network_id` (it used to read `network_name`, a
copy-paste bug); claims are type-asserted safely.

### 1.2 Verified JWT validation path — **OPEN**

`parseByJwtClientId` still calls `ParseUnverified`. The client only feeds it a
token obtained from its own API over TLS, and unverified parsing is documented
as non-authoritative, but the SDK path (`ClientAuth.ClientId`) remains
unverified. Decision needed: verified validation vs. explicit “parse-only”
contract.

## Phase 2 — egress policy

### 2.1 Explicit plaintext-egress policy with tests — **OPEN**

`egressSecurityPolicy.inspect` exists in `lib/connect/ip_security.go:57-76`
(UDP/53 and TCP/80 are temporarily allowed), but there are no tests for the
policy and no decision whether plaintext egress should be blocked by default.

### 2.2 DoH/DoT before blocking UDP/53 — **OPEN**

Not implemented. Plaintext DNS/HTTP currently work; nothing is blocked, so the
client cannot yet be switched to “block plaintext” mode without breaking
resolution.

## Phase 3 — packet construction

### 3.1 No panic on input-derived data — **PARTIAL**

Panics still exist: 9 `panic(` in `lib/connect/ip_packet.go` and 4 in
`lib/connect/ip.go`. The client-side mitigation is containment only —
`recover` around packet parsing at `lib/connect/ip.go:2999` turns a malformed
peer packet into an error. Finishing the ticket means error-returning APIs and
dropping invalid packets at callers.

### 3.2 Sentinel errors instead of `fmt.Errorf("Timeout.")` — **OPEN**

Sites: `lib/connect/net_http.go:710,755,788`;
`lib/connect/transport_pt.go:753,767`. Callers cannot reliably distinguish a
timeout from other failures.

## Phase 4 — hygiene

### 4.1 Trailing `\n` in `glog.*f` calls — **OPEN**

90 matches across `lib/` and `cmd/`. `glog` adds its own newline, so the
messages contain a blank line. Mechanical cleanup, do it after behaviour fixes.

### 4.2 `message_pool` ownership audit — **OPEN**

`lib/connect/message_pool.go` has `Get`/`Return`, but ownership rules are not
documented or enforced (double-return / use-after-return are not detected).

## Verification protocol

```bash
# client tests and static checks (used by CI)
make test
make vet

# the vendored libraries, module by module, sequentially
GOTOOLCHAIN=go1.26.0 go test ./...          # run inside each lib/* module
```

`Makefile` pins `GOTOOLCHAIN=go1.26.0` (the earlier revision of this document
mentioned `go1.25.0` — that was the toolchain cache of the original machine).
The integration tests in `lib/connect` / `lib/proxy` bind fixed local ports, so
they are **excluded from CI** on purpose and must not be run concurrently; run
them module-by-module when touching those packages.

## Notes

- The vendored trees are plain files, not submodules; see
  [`THIRD_PARTY.md`](../THIRD_PARTY.md) for the recorded base commits and the
  manual-sync procedure.
- [`docs/code-review.md`](code-review.md) documents the original findings; this
  file tracks their status. Keep both in sync when closing a ticket.
