# Third-party components

This repository vendors several Go libraries that are developed upstream under
the [Mozilla Public License 2.0](LICENSE). The source trees are committed as
regular files — there are no git submodules and no nested `.git` directories, so
a plain `git clone` of this repository is enough to build. `go.mod` files use
`replace` directives to point at these local copies.

| Path | Upstream | Base commit | License |
|---|---|---|---|
| `lib/connect` | https://github.com/urnetwork/connect | `d5d163f` | MPL-2.0 |
| `lib/sdk` | https://github.com/urnetwork/sdk | `ba24518` | MPL-2.0 |
| `lib/proxy` | https://github.com/urnetwork/proxy | `19d428f` | MPL-2.0 |
| `lib/userwireguard` | https://github.com/urnetwork/userwireguard | `f011cff` | MPL-2.0 |
| `lib/glog` | https://github.com/urnetwork/glog | `2aff5d7` | MPL-2.0 |

Each vendored tree keeps its own `LICENSE` file. The base commits above were
recorded when the trees were imported; they cannot be re-derived from this
repository because the nested history was not included.

## Local modifications

Changes made by this project on top of the upstream base commits:

- **`lib/connect`**
  - `ip.go`: `ParseIpPathWithPayload` rejects empty packets and recovers from
    malformed input instead of panicking on a peer-supplied packet
    (`lib/connect/ip.go:2999`).
  - `jwt.go`: `NetworkId` is read from the `network_id` claim (was
    `network_name`); claims use type-safe string assertions.
  - `net_tls.go`: `DefaultTlsConfig` requires TLS 1.3.
- **`lib/proxy`**
  - `socks/main.go`: default listen address `127.0.0.1:9999`, bind errors are
    reported, per-connection `Dialing` logs are behind `--verbose`, warning on
    non-loopback binds, empty packets are skipped.
  - `tun.go`: larger receive channel (512), 1 s write timeout, dial-race
    attempts rotate through resolved addresses.
- **`lib/userwireguard`**, **`lib/glog`**: no separate local patches are
  recorded for these two trees. The fixes tracked as tickets 0.1, 0.2 and 0.4 in
  `docs/refactoring-plan.md` are present in the vendored snapshot (the import
  predates the nested history being dropped, so it is not possible to say from
  this repository alone whether they were upstream or applied during import).

All other Go modules are resolved from the module proxy; see the `go.mod` /
`go.sum` files next to each module.

## Syncing with upstream

Because there is no nested history, syncing is a manual diff: fetch the upstream
repository at the commit you want, compare the file(s) you need against the
vendored copy, and commit the merged result as ordinary files. Keep the
`Base commit` column up to date when you do this, and note any conflicts with
the local modifications above.

The upstream libraries still have known issues of their own (unverified JWT
parsing, plaintext DNS/HTTP egress policy, panic-on-input helpers, trailing
newlines in `glog` calls). They are tracked in
[`docs/refactoring-plan.md`](docs/refactoring-plan.md) and
[`docs/code-review.md`](docs/code-review.md) and are outside the scope of the
client changes in this repository.
