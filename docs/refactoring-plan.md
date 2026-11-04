# Refactoring and Remediation Plan

**Target:** `urnetwork-client`
**Updated:** 2026-06-06
**Status:** Active

## Objective

Restore and protect a green test baseline first, then address verified security and architecture issues in small, test-backed changes. Do not start broad cleanup until compile and integration tests are reliable.

## Phase 0: Test Baseline Stabilization

Status: in progress; first pass implemented.

### Ticket 0.1: Fix `lib/userwireguard` bind interface drift

- **Target:** `lib/userwireguard/conn`, `lib/userwireguard/device`
- **Action:** Update test/fake binds to match `Bind.Open(bindIpv4, bindIpv6, port)` and preserve the selected IPv4 port when IPv6 bind is unavailable.
- **Verification:** `GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...` in `lib/userwireguard`.

### Ticket 0.2: Fix `lib/proxy` WireGuard test on hosts without IPv6 loopback

- **Target:** `lib/proxy`, via `lib/userwireguard/conn/bind_std.go`
- **Action:** Allow IPv4-only bind to continue when IPv6 address binding returns `EADDRNOTAVAIL`.
- **Verification:** `GOTOOLCHAIN=auto GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...` in `lib/proxy`.

### Ticket 0.3: Stabilize extender integration test

- **Target:** `lib/connect/extender/extender_test.go`
- **Action:** Replace privileged/fixed ports with ephemeral listeners and explicit readiness checks.
- **Verification:** `GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./extender` in `lib/connect`.

### Ticket 0.4: Restore glog default file sink behavior

- **Target:** `lib/glog/glog_file.go`
- **Action:** Register the file sink by default and restore `os.TempDir()` as the default log directory fallback.
- **Verification:** `GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...` in `lib/glog`.

## Phase 1: JWT Correctness and Trust Boundaries

### Ticket 1.1: Fix JWT claim parsing bug

- **Target:** `lib/connect/jwt.go`
- **Action:** Read `NetworkId` from `network_id`, not `network_name`; add table-driven tests for every parsed claim.
- **Verification:** forged/malformed/missing-claim tests prove parser behavior is explicit.

### Ticket 1.2: Define verified JWT validation path

- **Target:** `lib/connect/jwt.go`, `lib/connect/transport.go`, `lib/connect/ip_remote_multi_client_api.go`, SDK JWT helpers.
- **Action:** Separate parse-only display helpers from trust-sensitive validation helpers. Add a verified parser using the platform's public key or configured secret.
- **Verification:** tests reject unsigned, forged, wrong-algorithm, expired, and wrong-audience tokens where validation is required.

## Phase 2: Network Security Policy

### Ticket 2.1: Make plaintext egress policy explicit

- **Target:** `lib/connect/ip_security.go`
- **Action:** Decide whether UDP/53 and TCP/80 are allowed, blocked, or upgraded. Convert the decision into unit tests for `egressSecurityPolicy.inspect`.
- **Verification:** tests cover DNS, HTTP, HTTPS, DoT, mail ports, user ports, private IPs, blocked IPv4s, and IPv6 behavior.

### Ticket 2.2: Add protocol upgrade only after policy is tested

- **Target:** `lib/connect/net_http_doh.go`, IP egress path.
- **Action:** If DNS must work in public mode, implement DoH/DoT translation before removing UDP/53.
- **Verification:** packet trace or integration test proves no plaintext DNS/HTTP egress in the protected mode.

## Phase 3: Panic and Error Handling

### Ticket 3.1: Remove panic-on-input packet paths

- **Target:** `lib/connect/ip_packet.go` and callers.
- **Action:** Change packet builders/parsers that handle network-derived metadata to return errors instead of panicking.
- **Verification:** malformed protocol/version tests and fuzz target prove invalid inputs return errors.

### Ticket 3.2: Introduce sentinel/wrapped errors where useful

- **Target:** `lib/connect/net_http.go`, provider/auth paths.
- **Action:** Replace repeated `fmt.Errorf("Timeout.")` with sentinel errors; use `%w` where callers need `errors.Is` or `errors.As`.
- **Verification:** tests cover timeout detection and wrapped API errors.

## Phase 4: Cleanup After Behavior Is Stable

### Ticket 4.1: Scope and clean trailing newlines in glog calls

- **Target:** non-generated production Go files.
- **Action:** Remove trailing `\n` only from `glog.*f` format strings where glog already appends line endings.
- **Verification:** grep production files for `glog.*f` format strings ending in `\n`.

### Ticket 4.2: Audit message pool ownership

- **Target:** `lib/connect/message_pool.go` consumers.
- **Action:** Document ownership at risky async boundaries and add debug-only leak detection if practical.
- **Verification:** focused tests for error paths that previously could skip `MessagePoolReturn`.

## Verification Protocol

Run module tests sequentially because several packages use local ports:

```bash
cd lib/userwireguard && GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
cd lib/proxy && GOTOOLCHAIN=auto GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
cd lib/connect && GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
cd lib/sdk && GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
cd lib/glog && GOTOOLCHAIN=go1.25.0 GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
cd cmd/urnetwork-vpn && GOTOOLCHAIN=auto GOCACHE=/tmp/urnetwork-go-cache /usr/local/go/bin/go test ./...
```

Do not run `lib/connect`, `lib/sdk`, and `lib/proxy` integration tests in parallel until their fixed-port assumptions are removed.
