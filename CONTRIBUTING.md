# Как участвовать / Contributing

## Сборка и тесты

```bash
make build        # build/urnetwork-vpn, build/socksproxy
make test         # go test + pytest (44) + bash -n + py_compile
make vet          # go vet
make fmt          # gofmt
make deb          # dist/urnetwork-client_<версия>_<arch>.deb (нужен build)
make install      # NOPASSWD-копии в /usr/local/libexec/urnetwork (нужен root)
```

Для Go нужен toolchain >= 1.26: `make` выставляет `GOTOOLCHAIN=go1.26.0`
(дистрибутивный `/usr/bin/go` 1.18 этого не умеет — Makefile сам выбирает
`/usr/local/go/bin/go`, если он есть).

## Структура

| Путь | Что там |
|---|---|
| `cmd/urnetwork-vpn` | CLI/демон TUN-режима + Go-тесты |
| `lib/proxy` | SOCKS5-прокси (`lib/proxy/socks`) и gVisor-стек (`tun.go`) |
| `lib/connect`, `lib/sdk`, `lib/userwireguard`, `lib/glog` | вендоренные upstream-библиотеки обычными файлами, без сабмодулей (см. `THIRD_PARTY.md`) |
| `gui/` | GTK3-GUI, иконка, шаблон desktop-entry |
| `scripts/` | launcher'ы, askpass/keyring-хелперы, установщики, сборка `.deb` |
| `tests/` | pytest: GUI-логика, askpass, keyring, desktop-entry, статический аудит скриптов |
| `man/man1/` | man-страницы (устанавливаются `.deb`) |
| `docs/` | анализ, code review, план рефакторинга библиотек |

## Правила

- Не коммитьте `build/`, `dist/`, `~/.config/urnetwork/*` и секреты; пароли —
  только через keyring/окружение.
- Новый код — с тестом (`cmd/urnetwork-vpn/*_test.go` или `tests/`).
- Скрипты держите совместимыми с обоими раскладками — репозиторий и
  `/usr/lib/urnetwork` из `.deb`; `tests/test_connect_script_static.py`
  проверяет это статически (нет `local` вне функций, нет секретов в sudoers).
- Desktop-entry хранится как шаблон `gui/urnetwork-vpn.desktop.in` с
  `@PROJECT_DIR@`; абсолютные пути машины в git не попадают.
- Вендоренные `lib/*` — обычные файлы: правки коммитятся как обычные файлы,
  обновление upstream — ручной diff (см. `THIRD_PARTY.md`).
- Перед PR: `make test`, `make vet`, `gofmt -l` пуст.

## Релиз

1. Поднять версию: `VERSION` в `scripts/build-deb.sh` (дефолт) и `.TH`-строки
   в `man/man1/*.1`; при необходимости обновить `docs/analysis.md` §8.
2. `make test && make deb` — пакет должен пройти `lintian` (допустимо одно
   переопределение `initial-upload-closes-no-bugs`).
3. Коммит, тег `vX.Y.Z`, `git push origin master --tags` — release-workflow
   соберёт `.deb`/бинарники/`SHA256SUMS` и опубликует релиз.
