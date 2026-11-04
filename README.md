# URnetwork VPN client для Ubuntu

Клиент P2P-VPN на базе URnetwork: Go-стек (`lib/connect`, `lib/sdk`, `lib/proxy`),
CLI/демоны и GTK3-GUI на Python.

Клиент умеет работать в двух режимах:

| Режим | Что запускается | Как трафик попадает в туннель |
|---|---|---|
| SOCKS5 (без root) | `build/socksproxy` | Приложение/система идёт через `127.0.0.1:9999` |
| TUN (нужен root) | `build/urnetwork-vpn` | Default route уводится в устройство `urnetwork` |

## Быстрый старт

```bash
# 1. Сборка (нужен Go >= 1.26: make ставит GOTOOLCHAIN=go1.26.0)
make build

# 2. Регистрация аккаунта (один раз)
scripts/urnetwork-connect.sh register you@example.com 'пароль'

# 3. Запуск
./scripts/urnetwork-gui-launch.sh          # GUI
sudo scripts/urnetwork-connect.sh vpn      # VPN в терминале
scripts/urnetwork-connect.sh socks         # SOCKS5 в терминале
scripts/urnetwork-connect.sh status        # что сейчас запущено
sudo scripts/urnetwork-connect.sh off      # остановить всё и вернуть маршруты
```

GUI: `scripts/install-desktop-entry.sh` (пункт меню), затем запуск «URnetwork VPN».

## Конфигурация

`~/.config/urnetwork/` (права 0700):

| Файл | Назначение |
|---|---|
| `auth` | `USER_AUTH` / `PASSWORD`, права `0600` |
| `diagnostic.log` | лог GUI, ротация на 10 МБ в `diagnostic.log.1` |
| `launcher.log` | лог обёртки desktop-entry |
| `state.json` | состояние туннеля (пишет `urnetwork-vpn`) |
| `connect.lock` | защита от двух одновременных запусков |
| `default-route.before` | маршрут по умолчанию до подключения (для восстановления) |

Креды можно не писать в файл, а передавать через окружение:
`USER_AUTH=... PASSWORD=... build/socksproxy`.

## Безопасность

- Пароль не попадает в `argv`: и GUI, и скрипт передают его только через
  окружение (`ps` его не показывает).
- Пароль URnetwork **по умолчанию хранится в системном хранилище секретов**
  (GNOME Keyring / Secret Service) через `scripts/urnetwork-credentials.py`.
  В `~/.config/urnetwork/auth` остаются только `USER_AUTH`/`NETWORK_NAME`;
  старый пароль из файла при первом запуске автоматически переносится в
  keyring. Если секретного сервиса нет (SSH, голый WM), GUI падает обратно на
  файл с правами `0600`. Отключить keyring принудительно:
  `URNETWORK_KEYRING=0`.
- `scripts/install-nopasswd.sh` **больше не выдаёт NOPASSWD на файлы в git-чекауте**.
  Он копирует launcher и бинарники в `/usr/local/libexec/` (владелец root) и
  разрешает sudo только на эту копию. Старый вариант (`NOPASSWD` на
  user-writable скрипт + `/usr/sbin/ip`) был эквивалентен passwordless root.
  После `make build` или правки launcher'а установщик нужно запустить заново.
- SOCKS5 по умолчанию слушает только `127.0.0.1:9999`. Если открыть его наружу,
  клиент предупредит: аутентификации в SOCKS-сервере нет.
- TLS: минимальная версия 1.3 для API и control-соединений.

### Пароль sudo в отдельном окне терминала

У GUI нет терминала, поэтому `sudo` не может спросить пароль сам. Когда пароль
действительно нужен, GUI передаёт `SUDO_ASKPASS=scripts/urnetwork-askpass.sh`,
и помощник открывает отдельное окно терминала; набранный пароль уходит прямо в
`sudo` (не через `argv` и не через файл на диске). Если установлено правило из
`scripts/install-nopasswd.sh`, окно не появляется вообще — `sudo` ничего не
спрашивает. Подробности — в
[`app_analysis_and_improvements.md`](app_analysis_and_improvements.md), п. 8.8.

## Что изменилось в этой итерации

Полный разбор с числами — в [`app_analysis_and_improvements.md`](app_analysis_and_improvements.md).

- **VPN-ядро** (`cmd/urnetwork-vpn/main.go`): раньше каждый пакет печатался в
  stdout (`[TUN>>]`/`[<<TUN]`) и отправлялся синхронно с таймаутом 15 c — при
  заполнении pipe или медленном провайдере forwarding вставал. Теперь: пакеты
  не логируются без `--verbose`, отправка идёт через ограниченную очередь
  (`--send-queue`, по умолчанию 2048) и воркеров (`--send-workers`), буфер
  чтения переиспользуется, раз в 10 c печатается строка статистики
  (`stats: tx ... rx ... dropped=... queue=...`), состояние пишется в
  `state.json`.
- **Маршрутизация**: дефолтный маршрут сохраняется в `default-route.before`,
  `off`/`trap` его восстанавливают; при падении процесса GUI сам вызывает
  `off`. Добавлена «чёрная дыра» для IPv6 (`--block-ipv6`, включена по
  умолчанию), чтобы IPv6 не утекал мимо туннеля; MTU берётся из `--mtu` (раньше
  был жёстко зашит 1400).
- **TUN-устройство**: `createTun` больше не удаляет чужой `urnetwork` перед
  открытием; вместо этого — понятная ошибка и флаг `--replace-tun`. Именно
  удаление чужого устройства давало в логах `tun read error: bad address` и
  `Cannot find device "urnetwork"`.
- **SOCKS-прокси** (`lib/proxy/socks/main.go`): слушает loopback, ошибка bind
  больше не игнорируется, пустые пакеты и ошибки чтения не отправляются в
  туннель, логи `Dialing` — только с `--verbose`.
- **Прокси-стек** (`lib/proxy/tun.go`): буфер канала 512 пакетов (было 32),
  `WriteTimeout` 1 c, адрес выбирается по кругу между попытками dial, а не
  случайно.
- **Библиотека** (`lib/connect`): устранён crash на пустом пакете от пира
  (`ParseIpPathWithPayload`), JWT больше не паникует на не-строковых claim'ах,
  `NetworkId` читается из `network_id` (был copy-paste из `network_name`),
  TLS 1.3.
- **GUI** (`gui/urnetwork-gui.py`): пароль URnetwork хранится в системном
  хранилище секретов (GNOME Keyring), в `auth` — только аккаунт/сеть;
  `auth` пишется атомарно с правами `0600`;
  чтение stdout не блокирует GTK-цикл (неблокирующий fd + построчный буфер);
  логи банятся в UI (glog-строки скрыты, пока не включён verbose) и на диск
  пишутся только отображаемые строки — раньше `diagnostic.log` вырос до 1.6 ГБ;
  SIGTERM → SIGKILL через 5 c; при любом завершении движка вызывается `off`;
  один экземпляр GUI (flock); кнопки системного прокси активны только в
  SOCKS-режиме.
- **Скрипты** (`scripts/urnetwork-connect.sh`): `auth` парсится, а не
  `source`-ится; kredы только через env; `flock` от двойного запуска; рестарт
  при падении (раньше `wait $pid` под `set -e` убивал скрипт до retry);
  восстановление маршрута и удаление stale TUN; `register` качает бинарник через
  `mktemp` и раскладывает без старого бага `tar A || tar B && cp`.
- **Тесты/сборка**: `make test` (Go-тесты клиента + pytest + `bash -n`),
  `make build`, `make install`, CI-workflow.

## Известные ограничения

- Нет IPv6 внутри туннеля: IPv6-трафик либо блокируется, либо идёт мимо
  (см. `--block-ipv6`).
- SOCKS5 без аутентификации — не открывайте наружу.
- `lib/connect` содержит исторические проблемы, разобранные в
  `final_code_review_report.md` и `refactoring_plan.md`; они не входят в
  текущую итерацию.

## Проверка после изменений

```bash
make test            # Go + GUI + синтаксис скриптов
make build           # build/urnetwork-vpn, build/socksproxy
python3 -m py_compile gui/urnetwork-gui.py
```

Live-проверка SOCKS5 (egress-IP должен отличаться от прямого; пароль берётся
из системного хранилища, поэтому `auth` больше не содержит `PASSWORD=`):

```bash
export USER_AUTH=you@example.com
export PASSWORD="$(scripts/urnetwork-credentials.py get "$USER_AUTH")"
build/socksproxy & sleep 4
curl -s --socks5-hostname 127.0.0.1:9999 https://api.ipify.org; echo
kill %1
```

Live-проверка TUN (нужен root; при отсутствии правила sudo откроется окно
терминала с askpass):

```bash
bash scripts/urnetwork-connect.sh vpn      # в другом терминале
ip route show default                      # default dev urnetwork … metric 10
ip -6 route show | grep blackhole          # blackhole default … (IPv6 закрыт)
bash scripts/urnetwork-connect.sh off      # маршрут восстанавливается из backup
```
