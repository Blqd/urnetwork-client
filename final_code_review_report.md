# Code Review & Test Baseline Report

**Date:** 2026-06-06
**Scope:** `urnetwork-client` Go modules, networking/security review findings, and local test baseline.

## Executive Summary

The original audit direction was useful, but several claims were overstated or had incorrect line references. The immediate project risk was not only security debt: the local test baseline was broken across `lib/connect`, `lib/proxy`, `lib/userwireguard`, and `lib/glog`.

The first remediation pass restored the core test baseline by fixing stale WireGuard bind test adapters, IPv6 bind fallback behavior, unstable extender test ports, and glog file-sink initialization. Security remediation should proceed after this green baseline is preserved.

## Verified Security Findings

### High: Unverified JWT parsing

- **Location:** `lib/connect/jwt.go`
- **Evidence:** `ParseByJwtUnverified` calls `gojwt.ParseUnverified`.
- **Usage:** Parsed claims are used in transport/client selection paths, including `ClientAuth.ClientId()` and multi-client auth setup.
- **Risk:** If any local authorization or routing path trusts these claims without server-side verification, forged JWT claims can influence identity or routing behavior.
- **Correction:** The previous report's blanket claim that attackers can impersonate users should be treated as conditional until all downstream trust boundaries are mapped.
- **Action:** Replace unverified parsing in trust-sensitive paths with verified JWT validation, or explicitly mark parse-only helpers as non-authoritative and keep them out of authorization decisions.

### High: JWT claim correctness bug

- **Location:** `lib/connect/jwt.go`
- **Evidence:** `NetworkId` is read from `claims["network_name"]`; analogous SDK parsing reads `claims["network_id"]`.
- **Risk:** Network ID is silently unset or misparsed.
- **Action:** Fix the claim key and add unit tests for `user_id`, `network_name`, `network_id`, and `client_id`.

### Medium: Plaintext DNS/HTTP egress allowed

- **Location:** `lib/connect/ip_security.go`
- **Evidence:** UDP/53 and TCP/80 are temporarily allowed pending protocol translation.
- **Risk:** Plaintext DNS and HTTP may leave the client in public provide modes.
- **Action:** Decide product policy first: either block by default or add DoH/DoT/HTTPS upgrade behavior with tests and packet-level verification.

### Medium: Panic paths on packet construction

- **Location:** `lib/connect/ip_packet.go`
- **Evidence:** unsupported IP versions/protocols panic.
- **Risk:** Depending on call path, malformed or unexpected packet metadata can tear down goroutines or sessions. Some goroutines use `HandleError`, so "crashes the node" is not proven globally.
- **Action:** Return errors for untrusted packet inputs and drop/log invalid packets at callers.

## Corrected Informational Findings

- `DefaultTlsConfig()` uses `tls.VersionTLS12`. TLS 1.3-only may be desirable, but TLS 1.2 can still provide forward secrecy with the right cipher suites. Treat this as a compatibility/security policy decision.
- WebRTC uses public STUN servers in `transport_p2p_webrtc.go`; the correct location is the default WebRTC settings block, not the previously cited line.
- The `glog.*f(... "\n")` issue exists, but the earlier "over 252" count mixed generated files/tests/non-glog output. Use a scoped grep before turning it into a cleanup ticket.

## Test Baseline

Commands used:

```bash
GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
GOTOOLCHAIN=auto GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
```

Current remediation pass:

- `lib/userwireguard`: fixed stale `Bind.Open` test adapters and IPv6 bind fallback.
- `lib/proxy`: fixed by the shared `userwireguard` bind fallback.
- `lib/connect/extender`: fixed unstable test ports and privileged content port usage.
- `lib/glog`: restored default file sink registration and temp log directory fallback.

## Remaining Work

1. Keep the test baseline green with sequential module tests; several integration tests use local ports and should not be run concurrently.
2. Add focused JWT parsing tests and fix the `network_id` bug.
3. Decide JWT verification architecture before removing all unverified parsing.
4. Convert plaintext egress policy into executable tests.
5. Replace panic-on-input packet helpers with error-returning APIs.
6. Clean up scoped `glog` trailing newlines after behavior fixes are complete.
