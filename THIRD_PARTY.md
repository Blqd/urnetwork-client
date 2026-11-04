# Third-party components

This repository vendors several Go libraries that are developed upstream under
the [Mozilla Public License 2.0](LICENSE). Their source trees are tracked as
regular files (not git submodules) so that the client builds from a single
clone; `go.mod` files use `replace` directives to point at these local copies.

| Path | Upstream | Base commit | License |
|---|---|---|---|
| `lib/connect` | https://github.com/urnetwork/connect | `d5d163f` | MPL-2.0 |
| `lib/sdk` | https://github.com/urnetwork/sdk | `ba24518` | MPL-2.0 |
| `lib/proxy` | https://github.com/urnetwork/proxy | `19d428f` | MPL-2.0 |
| `lib/userwireguard` | https://github.com/urnetwork/userwireguard | `f011cff` | MPL-2.0 |
| `lib/glog` | https://github.com/urnetwork/glog | `2aff5d7` | MPL-2.0 |

Each vendored tree keeps its own `LICENSE` file.

## Local modifications

Changes made by this project on top of the upstream base commits:

- **`lib/connect`**
  - `ip.go`: `ParseIpPathWithPayload` rejects empty packets and recovers from
    malformed input instead of panicking on a peer-supplied packet.
  - `jwt.go`: `NetworkId` is read from the `network_id` claim (was
    `network_name`); claims use type-safe string assertions.
  - `net_tls.go`: `DefaultTlsConfig` requires TLS 1.3.
- **`lib/proxy`**
  - `socks/main.go`: default listen address `127.0.0.1:9999`, bind errors are
    reported, per-connection `Dialing` logs are behind `--verbose`, warning on
    non-loopback binds, empty packets are skipped.
  - `tun.go`: larger receive channel (512), 1 s write timeout, dial-race
    attempts rotate through resolved addresses.

All other Go modules are resolved from the module proxy; see the `go.mod` /
`go.sum` files next to each module.
