# Как участвовать / Contributing

## Сборка и тесты

```bash
make build        # build/urnetwork-vpn, build/socksproxy
make test         # go test + pytest + bash -n
make vet          # go vet
make fmt          # gofmt
```

Для Go нужен toolchain >= 1.26: `make` выставляет `GOTOOLCHAIN=go1.26.0`
(дистрибутивный `/usr/bin/go` 1.18 этого не умеет — Makefile сам выбирает
`/usr/local/go/bin/go`, если он есть).

## Структура

| Путь | Что там |
|---|---|
| `cmd/urnetwork-vpn` | CLI/демон TUN-режима |
| `lib/proxy` | SOCKS5-прокси (`lib/proxy/socks`) и gVisor-стек (`tun.go`) |
| `lib/connect`, `lib/sdk`, `lib/userwireguard`, `lib/glog` | вендоренные upstream-библиотеки (см. `THIRD_PARTY.md`) |
| `gui/` | GTK3-GUI, иконка, шаблон desktop-entry |
| `scripts/` | launcher'ы, askpass/keyring-хелперы, установщики |
| `tests/` | pytest: GUI-логика, askpass, keyring, desktop-entry |
| `docs/` | анализ, code review, план рефакторинга |

## Правила

- Не коммитьте `build/`, `~/.config/urnetwork/*` и секреты; пароли — только
  через keyring/окружение.
- Новый код — с тестом (`cmd/urnetwork-vpn/*_test.go` или `tests/`).
- Desktop-entry хранится как шаблон `gui/urnetwork-vpn.desktop.in` с
  `@PROJECT_DIR@`; абсолютные пути машины в git не попадают.
- Перед PR: `make test`, `make vet`, `gofmt -l` пуст.
