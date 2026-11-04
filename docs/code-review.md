# Code Review & Test Baseline Report

**Date:** 2026-06-06 (original report); **status re-checked:** 2026-11-04
**Scope:** `urnetwork-client` Go modules, networking/security review findings, and local test baseline.

> **Status summary (2026-11-04).** The test-baseline remediation is complete —
> `lib/userwireguard`, `lib/proxy`, `lib/connect/extender` and `lib/glog` tests
> pass today. The JWT claim bug is fixed. Unverified JWT parsing, the plaintext
> egress policy and the panic-on-input helpers are still open; tracked ticket by
> ticket in [`refactoring-plan.md`](refactoring-plan.md).
> The client-side hardening done after the client code review (sudoers
> `env_keep`, credentials file, state-path restriction, pid verification, shutdown
> behaviour) is described in [`analysis.md`](analysis.md) §8.11.

## Executive Summary

The original audit direction was useful, but several claims were overstated or had incorrect line references. The immediate project risk was not only security debt: the local test baseline was broken across `lib/connect`, `lib/proxy`, `lib/userwireguard`, and `lib/glog`.

The first remediation pass restored the core test baseline by fixing stale WireGuard bind test adapters, IPv6 bind fallback behavior, unstable extender test ports, and glog file-sink initialization. Security remediation should proceed after this green baseline is preserved.

## Verified Security Findings

### High: Unverified JWT parsing — OPEN

- **Location:** `lib/connect/jwt.go`
- **Evidence:** `ParseByJwtUnverified` calls `gojwt.ParseUnverified`.
- **Usage:** Parsed claims are used in transport/client selection paths, including `ClientAuth.ClientId()` and multi-client auth setup.
- **Risk:** If any local authorization or routing path trusts these claims without server-side verification, forged JWT claims can influence identity or routing behavior.
- **Correction:** The previous report's blanket claim that attackers can impersonate users should be treated as conditional until all downstream trust boundaries are mapped.
- **Action:** Replace unverified parsing in trust-sensitive paths with verified JWT validation, or explicitly mark parse-only helpers as non-authoritative and keep them out of authorization decisions. The client itself only feeds a token obtained from its own API over TLS; the SDK path is untouched.

### High: JWT claim correctness bug — DONE (2026-11-04)

- **Location:** `lib/connect/jwt.go`
- **Evidence:** `NetworkId` used to be read from `claims["network_name"]`; analogous SDK parsing reads `claims["network_id"]`.
- **Risk:** Network ID was silently unset or misparsed.
- **Action taken:** `lib/connect/jwt.go:33` reads `network_id` and type-asserts claims safely (no panic on non-string values).

### Medium: Plaintext DNS/HTTP egress allowed — OPEN

- **Location:** `lib/connect/ip_security.go`
- **Evidence:** `egressSecurityPolicy.inspect` (`lib/connect/ip_security.go:57-76`) temporarily allows UDP/53 and TCP/80 pending protocol translation.
- **Risk:** Plaintext DNS and HTTP may leave the client in public provide modes.
- **Action:** Decide product policy first: either block by default or add DoH/DoT/HTTPS upgrade behavior with tests and packet-level verification.

### Medium: Panic paths on packet construction — PARTIAL

- **Location:** `lib/connect/ip_packet.go`
- **Evidence:** unsupported IP versions/protocols panic (still 9 `panic(` in `ip_packet.go`, 4 in `ip.go`).
- **Risk:** Depending on call path, malformed or unexpected packet metadata can tear down goroutines or sessions. Some goroutines use `HandleError`, so "crashes the node" is not proven globally.
- **Mitigation in place:** `ParseIpPathWithPayload` rejects empty packets and recovers around parsing (`lib/connect/ip.go:2999`), so a peer-supplied malformed packet becomes an error instead of a crash.
- **Action:** Return errors for untrusted packet inputs and drop/log invalid packets at callers.

## Corrected Informational Findings

- `DefaultTlsConfig()` used `tls.VersionTLS12`; the vendored copy now requires TLS 1.3 (`lib/connect/net_tls.go`). This is a policy decision: it reduces compatibility with old peers but removes the TLS 1.2 surface.
- WebRTC uses public STUN servers in `transport_p2p_webrtc.go`; the correct location is the default WebRTC settings block, not the previously cited line. Unchanged.
- The `glog.*f(... "\n")` issue exists (90 matches today), but the earlier "over 252" count mixed generated files/tests/non-glog output. Use a scoped grep before turning it into a cleanup ticket.

## Test Baseline

Commands used originally:

```bash
GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
GOTOOLCHAIN=auto GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
```

Current state (2026-11-04): all four remediation items are in the tree and the
tests pass — `lib/userwireguard` (conn 0.005 s, device 1.194 s), `lib/proxy`,
`lib/connect/extender`, `lib/glog`. The project `Makefile` pins
`GOTOOLCHAIN=go1.26.0`, so use that (the `go1.25.0` above was the toolchain cache
of the original machine, not a requirement).

- `lib/userwireguard`: `Bind.Open` test adapters and IPv6 bind fallback — fixed.
- `lib/proxy`: fixed by the shared `userwireguard` bind fallback.
- `lib/connect/extender`: unstable test ports — fixed.
- `lib/glog`: default file sink registration and temp log directory fallback — fixed.

## Remaining Work

1. Keep the test baseline green with sequential module tests; several integration tests use local ports and should not be run concurrently (they are excluded from CI for this reason).
2. ~~Add focused JWT parsing tests and fix the `network_id` bug.~~ Done (`lib/connect/jwt.go:33`).
3. Decide JWT verification architecture before removing all unverified parsing (ticket 1.2).
4. Convert plaintext egress policy into executable tests (ticket 2.1) and implement DoH/DoT before blocking UDP/53 (ticket 2.2).
5. Replace panic-on-input packet helpers with error-returning APIs (ticket 3.1).
6. Clean up scoped `glog` trailing newlines after behavior fixes are complete (ticket 4.1).

Open tickets and their verification protocol live in
[`refactoring-plan.md`](refactoring-plan.md).
