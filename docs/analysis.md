# URnetwork VPN client: анализ работы и предложения по улучшению

**Дата:** 2026-11-04
**Проанализировано:** `cmd/urnetwork-vpn/main.go` (757 строк), `lib/proxy/socks/main.go` (511), `lib/proxy/tun.go` (513), `gui/urnetwork-gui.py` (563), `scripts/urnetwork-connect.sh` (350) + вспомогательные скрипты, `lib/connect/jwt.go`, `lib/connect/ip_security.go`, `lib/connect/ip_packet.go`, `lib/connect/message_pool.go`, `lib/connect/net_tls.go`, `lib/connect/transport_p2p_webrtc.go`, тесты `cmd/`, `tests/`, логи `build/vpn.log`, `build/socks.log`, `~/.config/urnetwork/diagnostic.log`.

**Что было сделано, помимо чтения кода:**
* собраны бинари и прогнаны тесты (`go build`, `go test`, `pytest`) — всё зелёное;
* запущен `build/socksproxy` с реальными учётными данными и проверен фактический egress через туннель;
* измерены скорость и задержка «напрямую vs через туннель»;
* разобраны логи предыдущих запусков.

---

## 0. Резюме

Приложение **работает**: SOCKS5-режим проверен вживую — трафик реально выходит через сеть URnetwork (внешний IP `67.85.246.40`, Brooklyn, NY вместо прямого `91.78.35.192`, Москва). TUN-режим поднимает интерфейс, адрес и маршрут по умолчанию и передаёт пакеты. Архитектура в целом разумная: два режима (userspace-стек без root и полноценный TUN с root), один REST/WS API, DoH внутри туннеля, единый формат устройств.

Основные проблемы сосредоточены не в библиотеке `lib/connect`, а в **обвязке вокруг неё** — CLI-бинарях, скрипте и GUI.

| # | Проблема | Где | Влияние |
|---|---|---|---|
| 1 | Печать строки на каждый пакет в stdout (в обе стороны) | `cmd/urnetwork-vpn/main.go:302,339`; `lib/proxy/socks/main.go:244` | Блокировка форвардинга при заполнении пайпа, 1.6 ГБ лога, зависания GUI, падение throughput |
| 2 | Синхронный `SendPacket(..., 15s)` в цикле чтения TUN | `main.go:341-346`, `socks/main.go:226-231` | Head-of-line blocking, дропы в очереди TUN, деградация под нагрузкой |
| 3 | Две аллокации + два копирования на каждый пакет | `main.go:610-620` | GC-churn, лишняя нагрузка на CPU при высоком PPS |
| 4 | `createTun` безусловно удаляет существующий интерфейс | `main.go:540-541` | Убивает параллельный/предыдущий инстанс; `tun read error: bad address` в логе |
| 5 | Нет IPv6 (ни в TUN, ни в socks-стеке) | `main.go:691-699`, `lib/proxy/tun.go:73-79` | Трафик приложений к IPv6-адресам уходит **мимо** туннеля; `curl -6` через socks не работает |
| 6 | Учётные данные в argv, файл `auth` с правами 0664 | `scripts/urnetwork-connect.sh:193-202`; `gui/urnetwork-gui.py:276-284` | Пароль виден в `ps` любому локальному пользователю и читается с диска |
| 7 | SOCKS5 слушает `:9999` (0.0.0.0, без аутентификации) | `lib/proxy/socks/main.go:59` | Открытый прокси в локальной сети |
| 8 | NOPASSWD в sudoers на user-writable скрипт и на `ip` | `scripts/install-nopasswd.sh:4-9` | Фактически passwordless root (`ip netns exec` = произвольная команда от root) |
| 9 | GUI не убирает за собой | `gui/urnetwork-gui.py:489-498,552-558,448-458` | Осиротевшие процессы, «залипшие» маршруты, стоп-кнопка может не сработать |
| 10 | `panic()` на битом пакете; `network_id` из claim `network_name`; JWT без проверки подписи | `lib/connect/ip_packet.go:20-32…`; `lib/connect/jwt.go:30-35,14-16` | Удалённый DoS клиента; неверный разбор claims; доверие неподписанным данным |

Плюс техдолг: `build/` устарел, Go-тулчейн не запинен, вложенные `.git` в `lib/*`, 11 МБ картинок в `lib/connect/res`, 66k-строчный сгенерированный `ip_security.go` (2.3 МБ) в каждой сборке, нет CI и README.

---

## 1. Как приложение устроено

### 1.1 Компоненты

```
gui/urnetwork-gui.py                 GTK3 GUI (Python/PyGObject)
        │  subprocess: scripts/urnetwork-connect.sh <socks|vpn> [--country|--city]
        ▼
scripts/urnetwork-connect.sh         оркестратор: загрузка кредов, sudo, PID-файлы,
        │                            pid/port-ожидание, retry, off/toggle/register/status
        ├── build/socksproxy         без root: userspace-стек gVisor + SOCKS5 :9999
        └── build/urnetwork-vpn      root: /dev/net/tun + маршруты + форвардинг пакетов
                │
                ▼
        lib/connect                  библиотека URnetwork: REST api.bringyour.com,
                                     WSS platform (connect.bringyour.com), P2P (WebRTC/QUIC),
                                     multi-client transfer (контракты/кредиты), message pool
        lib/proxy                    userspace TUN (gVisor) + SOCKS5/HTTP/WireGuard обвязки
        lib/sdk, lib/userwireguard   SDK и WireGuard-стек
```

### 1.2 SOCKS5-режим (без root)

1. `socksproxy` логинится (`POST /auth/...` → JWT), получает `GET /network/provider-locations`, выбирает провайдера (`getProviderSpec`, `lib/proxy/socks/main.go:289`), выполняет `auth-client` с `DeviceSpec: "socks5"` (`socks/main.go:162-170`).
2. Создаётся userspace-стек gVisor: `proxy.CreateTunWithDefaults` → `lib/proxy/tun.go:178-270` (один общий `stack.Stack`, NIC + IPv4-адрес из пула, канал размером 32, MTU 1440, DoH-резолвер, ходящий через этот же стек: `tun.go:229-240`).
3. `connect.NewRemoteUserNatMultiClient` связывает пакеты стека с P2P-сетью: `dev.Read()` → `mc.SendPacket(...)`, пришедшие пакеты → `dev.Write()` (`socks/main.go:208-238`).
4. Поднимается SOCKS5-сервер (`go-socks5`): каждый `CONNECT`/`UDP ASSOCIATE` вызывает `dev.DialContext(ctx, "tcp"|"udp", addr)` (`socks/main.go:240-257`) — т.е. **весь прикладной трафик идёт через туннель, DNS резолвится через DoH внутри туннеля** (адресация по FQDN, `tun.go:437-440`).

### 1.3 TUN-режим (root)

1. Те же шаги логина/выбора провайдера, `DeviceSpec: "vpn"` (`main.go:224-227`), затем `parseByJwtClientId` (`main.go:511`).
2. `createTun` открывает `/dev/net/tun`, создаёт интерфейс без PI (`IFF_TUN|IFF_NO_PI`, `main.go:551`).
3. `setupRouting` (`main.go:677-702`): bypass-маршруты до `api.bringyour.com`/`connect.bringyour.com` (резолвятся заранее, `main.go:654-668`), адрес `10.0.0.1/24`, `link up`, `route replace default dev urnetwork src 10.0.0.1 metric 10`.
4. Горoutine читает `/dev/net/tun` и отправляет пакеты в `mc.SendPacket` с `ProvideMode_Network`; callback из сети пишет пакеты обратно в TUN.
5. По `SIGINT/SIGTERM` — «Disconnecting...», затем `teardownRouting` удаляет маршруты/адрес.

### 1.4 Как это выглядит в данных

Пакеты с TUN уходят в сеть как есть (без изменений), шифрование/транспорт — на уровне transfer-протокола URnetwork. Трафик по умолчанию (`ProvideMode_Network`) **не проходит** через политику `DefaultEgressSecurityPolicy` — см. п. 4.3.2.

---

## 2. Живой запуск и измерения

### 2.1 Смоук-тест SOCKS5 (реально выполнен)

```
$ ./build/socksproxy  (env USER_AUTH/PASSWORD, ~/.config/urnetwork/auth)
auto-selected "United States" (country), provider count 29186
my clientID: 01a1a678-4cb0-58d9-3c3e-f4392b8393dd
socks5 server is listening on :9999
```

| Проверка | Результат |
|---|---|
| Прямой egress | `91.78.35.192` (Москва) |
| Egress через SOCKS5 | `67.85.246.40` → Brooklyn, NY, US, AS6128 (CABLE-NET-1) |
| DNS | FQDN резолвится на стороне выхода (DoH внутри туннеля), локального DNS-запроса нет |
| `curl -6 https://ipv6.google.com` через прокси | ошибка (rc=7) — IPv6 не поддерживается |

Вывод: **SOCKS5-путь рабочий end-to-end**, включая удалённый DNS.

### 2.2 Производительность (один и тот же exit-провайдер, 5 МБ с Cloudflare)

| Сценарий | Напрямую | Через SOCKS5 | Разница |
|---|---|---|---|
| 1 поток, 5 МБ | 3.13 МБ/с (1.60 с) | 0.66 МБ/с (7.55 с) | ≈4.7× медленнее |
| 4 потока × 5 МБ | 5.6 с (≈3.6 МБ/с суммарно) | 54.2 с (≈0.37 МБ/с суммарно) | ≈10× медленнее |
| 10 запросов `https://api.ipify.org` подряд | 0.34 с средняя | 1.28 с средняя | +0.94 с на запрос |

**Интерпретация.** Часть разницы — объективная (выход в Нью-Йорке, путь Москва→NY, ёмкость конкретного exit-пира). Но показательно, что при переходе с 1 на 4 параллельных потока туннель **деградирует** (0.66 → 0.37 МБ/с), тогда как прямое соединение масштабируется. Это согласуется с узким местом в клиенте: единый последовательный цикл `dev.Read() → mc.SendPacket(15s)`, канал gVisor на 32 пакета (`lib/proxy/tun.go:48`) и `WriteTimeout: 5s` (`tun.go:56`) — при RTT ~130 мс 32 пакета × 1440 Б ≈ 44 КБ в полёте, что как раз даёт порядок сотен КБ/с. Точную атрибуцию (клиент vs. сеть) нужно подтвердить после патчей п. 6.2: те же измерения на пропатченном бинаре.

### 2.3 Разбор логов прошлых запусков

| Сообщение в логе | Что реально происходит | Ссылка |
|---|---|---|
| `RTNETLINK answers: File exists` | повторная настройка addr/route при уже существующем интерфейсе (двойной запуск или повторный setup) | `main.go:691,697` |
| `Error: inet6 address is expected rather than "192.168.31.1".` | IPv6 bypass-префиксы пытались установить через IPv4-шлюз; сейчас отфильтровано | `main.go:717-720` |
| `tun read error: bad address` | `EFAULT`: файловый дескриптор TUN указывает на уже удалённое устройство — его удалил другой инстанс/`off` | `main.go:540-541`, `urnetwork-connect.sh:199,219` |
| `... exit idle timeout` (3 раза) | путь до пира умер; клиент продолжает слать в мёртвый путь, реконнекта нет | `lib/connect/transfer.go:2206` |
| `Cannot find device "urnetwork"` при teardown | устройство уже удалено, teardown не идемпотентен и шумит | `main.go:726-733` |
| `debit contract ... (13075/13107 total 99.8% full)` | исчерпан кредит/ёмкость выбранного exit-пира; клиенту следовало бы переключиться на другого и показать это в UI | `lib/connect/transfer.go:4416` |
| `message_pool.go:156 ... 98.79% return / 91.97% reuse` | пул работает, но ~8% буферов создаются заново → лишний GC | `lib/connect/message_pool.go` |
| `write: broken pipe` (socks.log) | клиент закрыл соединение, прокси писал в него; шум, не ошибка | `lib/proxy/socks/main.go` |
| `diagnostic.log` = **1 661 759 630 Б** (~1.6 ГБ), права 0664 | GUI пишет **каждую** строку (включая glog и per-packet) в файл без ротации | `gui/urnetwork-gui.py:307-313` |

---

## 3. Что уже сделано хорошо

* Чёткое разделение режимов: SOCKS5 без root (userspace netstack) и TUN с root — правильная идея, позволяет пользоваться без прав.
* DoH-резолвер, ходящий **внутри** туннеля (`lib/proxy/tun.go:229-240`) — DNS не утекает в локальный резолвер в socks-режиме.
* `IFF_NO_PI` и явная работа с `/dev/net/tun` — без лишних зависимостей.
* Bypass-маршруты для control-хостов до включения default route, фильтрация family-mismatch (`main.go:708-724`) — видно, что проблемы уже отлаживали.
* Есть тесты: `cmd/urnetwork-vpn/main_test.go` (6 тестов), 13 Python-тестов на десктоп-запись/иконку/статус GUI; они проходят.
* Launcher `.desktop` с диагностикой (лог, notify-send/zenity, коды выхода, выбор интерпретатора с `gi`) — грамотная обвязка для GTK-приложения.
* `glog` с file-sink, `message_pool` с диагностикой — хорошая база для наблюдаемости.

---

## 4. Проблемы

### 4.1 P0 — блокирующие и безопасность

#### 4.1.1 Печать на каждый пакет (главное узкое место)

`[TUN>>]`/`[<<TUN]` печатаются для **каждого** пакета в stdout без буфера:

```go
// cmd/urnetwork-vpn/main.go:339 (и 302 — обратное направление)
fmt.Printf("[TUN>>] %s %s->%s (%d bytes)\n", proto, srcIP, dstIP, len(packet))
```

`os.Stdout` не буферизован → каждый Printf — это `write(2)`. Если читатель (GUI, `tee`, systemd-journal, пайп) не успевает, пайп (64 КБ) заполняется — и **весь форвардинг пакетов встаёт**. При MTU 1400 это ~45 пакетов. GUI в это время вставляет каждую строку в `Gtk.TextView` (`gui/urnetwork-gui.py:293-298`), что на порядки медленнее сети. Итог: просадки, «зависания», рост `diagnostic.log`.

Дополнительно `fmt.Printf` в callback из сети (`main.go:302`) выполняется в горутине библиотеки — задерживает обработку входящих пакетов.

#### 4.1.2 Синхронная отправка в сеть из цикла чтения TUN

```go
// cmd/urnetwork-vpn/main.go:313-351
for {
    packet, err := tun.Read()
    ...
    ok := mc.SendPacket(source, protocol.ProvideMode_Network, packet, time.Second*15)
}
```

Один читатель, один синхронный вызов на пакет, дедлайн 15 с. Любая задержка `SendPacket` (конгестия, нет свободного клиента, backpressure) блокирует чтение TUN; ядро копит пакеты в очереди TUN и **молча дропает** при переполнении. В логах это выглядит как `SendPacket FAILED probable=tcp-collapse/backpressure/no-client` (`main.go:368`). То же в socks-режиме: `socks/main.go:222-238`.

#### 4.1.3 Две аллокации на каждый пакет

```go
// cmd/urnetwork-vpn/main.go:609-626
buf := make([]byte, t.mtu+64)      // #1
n, err := unix.Read(t.fd, buf)
packet := make([]byte, n)          // #2
copy(packet, buf[:n])
```

На 100k pps это 200k аллокаций/с и лишнее копирование 1.4 КБ. Аналогично `Tun.Write` в socks-режиме копирует пакет в gVisor (`lib/proxy/tun.go:296-299`), а `WriteNotify` — ещё раз (`tun.go:318-321`) и дропает через 5 с ожидания (`tun.go:324-332`).

#### 4.1.4 `createTun` удаляет чужой интерфейс

```go
// cmd/urnetwork-vpn/main.go:538-541
exec.Command("ip", "link", "delete", name).Run()
exec.Command("ip", "tuntap", "del", name, "mode", "tun").Run()
```

Безусловно, до открытия `/dev/net/tun`. Если VPN уже запущен (второй инстанс, GUI + терминал, повторный клик), у первого инстанса исчезает устройство: `unix.Read` возвращает `EFAULT` → в логе ровно `tun read error: bad address`, туннель умирает. То же делает `urnetwork-connect.sh:199,219,247` (`ip link delete urnetwork`) и GUI-путь `off`.

#### 4.1.5 Утечка IPv6

В TUN-режиме настраивается только IPv4-адрес и только IPv4 default route (`main.go:691-699`). Приложения, ходящие на IPv6 (а современные браузеры ходят по Happy Eyeballs), уходят через **обычный** шлюз мимо туннеля. В socks-стеке IPv6-протокол вообще не зарегистрирован (`lib/proxy/tun.go:73-79`) — проверено: `curl -6` через прокси падает.

#### 4.1.6 Учётные данные

* `--user-auth`/`--password` передаются **аргументами командной строки** (`urnetwork-connect.sh:193-194,200-202,238-241,249-252`) → видны в `ps aux` любому локальному пользователю. При этом бинарь уже умеет читать env `USER_AUTH`/`PASSWORD` (`main.go:135-140`, `socks/main.go:76-88`) — флаги не нужны.
* GUI пишет `~/.config/urnetwork/auth` обычным `open(..., "w")` без `chmod 600` (`gui/urnetwork-gui.py:276-284`). Проверено фактически: `-rw-rw-r-- (664)`. Скрипт `register` при этом ставит `chmod 600` (`urnetwork-connect.sh:287`) — поведение расходится в зависимости от пути записи.
* Файл `source`-ится шеллом (`urnetwork-connect.sh:154`), а GUI пишет значения **без квотирования** (`PASSWORD={}`), поэтому пароль с `$`, пробелом, кавычкой или backtick'ом ломает аутентификацию, а потенциально — исполняется при `source` (в т.ч. от root после `sudo`-перезапуска).
* `diagnostic.log` с правами 0664 содержит email, clientID и адреса назначения.

#### 4.1.7 SOCKS5 — открытый прокси

`Value: ":9999"` (`lib/proxy/socks/main.go:59`) — слушает **все интерфейсы**, аутентификации нет, ACL нет. Любой в LAN (или порт-форвард/публичный IP ноутбука в кафе) может пользоваться туннелем и жечь ваши кредиты. GUI при этом рекламирует `127.0.0.1:9999` (`gui/urnetwork-gui.py:537`) — то есть задумано локально, а слушается глобально.

#### 4.1.8 NOPASSWD sudoers = passwordless root

`scripts/install-nopasswd.sh:4-9` выдаёт `NOPASSWD` на:
* `scripts/urnetwork-connect.sh` — **пользовательский, редактируемый файл**: любая программа от имени пользователя может дописать `bash` и получить root;
* `build/urnetwork-vpn` — тоже user-writable бинарь;
* `/usr/sbin/ip` — а `ip netns exec <ns> <cmd>` исполняет произвольную команду от root.

То есть установка этого файла эквивалентна `ALL=(ALL) NOPASSWD: ALL`. Плюс `env_keep += "USER_AUTH PASSWORD"` протаскивает пароль в окружение root-процесса.

### 4.2 P1 — надёжность и UX

#### 4.2.1 Нет восстановления после аварий

* `teardownRouting` вызывается только через `defer` (`main.go:274`) — при `SIGKILL`/панике не выполняется. Bypass-маршруты (`ip route replace <cidr> via gw`, `main.go:686`) остаются в таблице навсегда; default через TUN исчезнет вместе с устройством, но «мусор» и перехват пользовательских CIDR остаются.
* При обрыве пути (лог `exit idle timeout`) клиент не пересоздаёт путь/не переавторизуется — трафик просто чёрная дыра, в UI «Connected».
* Скрипт не ретраит **краши**: `wait $pid` под `set -euo pipefail` (`urnetwork-connect.sh:2,75,86`) при ненулевом коде выходит из скрипта до блока retry — retry работает только для «порт не забиндился» (`:73-83`).
* Нет watchdog/keepalive/`--reconnect`, нет авто-переключения на другого провайдера при `99.8% full`.

#### 4.2.2 Жизненный цикл GUI

* `on_process_end` (`gui/urnetwork-gui.py:489-498`) сбрасывает только UI: не вызывает `off`, не убирает маршруты/прокси, не перезапускает.
* `on_destroy` (`:552-558`) вызывает `stop()` и сразу `Gtk.main_quit()` — запланированный `GLib.timeout_add(200, self._poll_stop)` (`:444`) уже не выполнится, `_finish_stop` с `off` (`:463`) не отработает → процесс может остаться, прокси/маршруты — тоже. В логе это видно как серия `⏹ Stopping...` и `GUI exited with status 137`.
* `_poll_stop` (`:448-458`) при неуступчивом процессе возвращает `True` вечно; SIGKILL-эскалации нет. `except:` без типа.
* `_finish_stop` выполняет `subprocess.run([SCRIPT, "off"], capture_output=True)` **в GTK-потоке**: если `sudo` попросит пароль (нет tty) — UI зависнет навсегда.
* `on_output` (`:472-482`) читает `self.process.stdout.readline()` (буферизованный текстовый поток) по событию готовности **fd**: смешение двух уровней буферизации — возможны «залипания» и потеря строк; `readline()` блокирует GTK, если пришла половина строки.
* `preexec_fn=os.setsid` (`:422`) при живом фоновом потоке (`fetch_locations`, `:350-375`) — недокументированно небезопасен (fork в многопоточном процессе); нужен `start_new_session=True`.
* Нет single-instance lock: два GUI могут стартовать два туннеля и убить друг друга через `createTun`.
* Прокси-кнопки (`:517-546`) работают независимо от режима: можно включить SOCKS-прокси в VPN-режиме, и после выхода он не сбрасывается.
* `NETWORK_NAME` (`:273,283`) собирается и сохраняется, но нигде не используется; `regions` парсится (`:322-330`) и игнорируется; нет egress-IP/трафика/латентности; нет трея/автозапуска.

#### 4.2.3 Скрипт

* Нет локов: два запуска (`GUI` + терминал) конфликтуют.
* `pkill -f "socksproxy"` / `pkill -f "urnetwork-vpn"` (`:217-218`) бьёт по всем процессам пользователя с этим именем в cmdline — может убить чужой/отладочный экземпляр.
* `require_root` (`:142-150`) перезапускает весь скрипт под `sudo`, таща `USER_AUTH`/`PASSWORD` в env.
* `off` (`:208-222`) не восстанавливает исходную маршрутную конфигурацию: если child уже мёртв, bypass-маршруты остаются; `ip link delete` без проверки владельца.
* `register` (`:255-291`): скачивает `urnetwork-provider` с GitHub по захардкоженному тегу **без контрольной суммы/подписи**; классическая ошибка приоритета — `tar ... || tar ... && cp` (`:273-275`) выполняет `cp` из `/tmp` даже когда первый `tar` успешен (и падает по `set -e`).
* `status` (`:320-341`) показывает только PID-файлы, не реальное состояние (нет TUN/маршрутов/прокси).

### 4.3 P2 — библиотека, техдолг, гигиена

#### 4.3.1 Безопасность/корректность библиотеки

* `ParseByJwtUnverified` (`lib/connect/jwt.go:14-16`) — `ParseUnverified`, подпись/срок не проверяются. Клиент использует это для `client_id` (`main.go:511-524`) и в `ClientAuth.ClientId`. Если решение сознательное — надо как минимум валидировать claims и брать `client_id` из ответа `auth-client`, а не из JWT.
* Явный copy-paste: `NetworkId` читается из `claims["network_name"]` (`lib/connect/jwt.go:33`) — id всегда нулевой, если сервер не кладёт id в поле `network_name`.
* `panic()` на невалидном пакете (`lib/connect/ip_packet.go:20,29,32,63,98,130,165,219,254…`) — пир может прислать битый пакет и **уронить клиент**. Нужны ошибки и дроп на уровне вызывающих.
* Egress-политика не применяется к клиенту: `ProvideMode_Network` короткозамыкается в `Allow` (`lib/connect/ip_security.go:84-86`), а клиент всегда шлёт с `ProvideMode_Network` (`main.go:343`) → 66k-строчный блоклист и порт-политика (включая разрешённые plaintext 53/80, `ip_security.go:121-131`) на клиентский трафик не влияют. Это стоит явно задокументировать (а «FIXME protocol translation» — закрыть).
* `MinVersion: tls.VersionTLS12` (`lib/connect/net_tls.go:43`) — поднять до 1.3.
* Захардкоженный список публичных STUN (`lib/connect/transport_p2p_webrtc.go:384-391`) — внешняя зависимость и метаданные; сделать конфигурируемым, дефолт — минимальный набор.
* P2P-транспорт не биндится к физическому интерфейсу (`SO_BINDTODEVICE`/`SO_MARK`): после поднятия default route любые **новые** соединения к пирам пойдут в туннель (рекурсия/чёрная дыра). Сейчас спасает только то, что шифрованный транспорт установлен заранее; это хрупко.

#### 4.3.2 Производительность библиотеки

* `message_pool` диагностирует 91.97% reuse — ~8% буферов создаются заново; стоит проверить размеры пулов и пути возврата (`ResizeMessagePools`).
* Канал gVisor 32 пакета и `WriteTimeout: 5s` (`lib/proxy/tun.go:48,56`) — вероятный потолок throughput (см. п. 2.2).
* `tun.DialContext` (`tun.go:392-415`): 4 попытки по 2 с, но каждая `dialContext` выбирает **один случайный** адрес из DoH-ответа (`tun.go:433`) без fallback по остальным (цикл закомментирован) → при «плохом» IP соединение падает после 30 с, и все 4 попытки могут выбрать тот же адрес.

#### 4.3.3 Сборка и репозиторий

* `build/` — устаревшие бинари: `socksproxy` и `urnetwork-vpn` от 7 июня, GUI изменён 3 ноября. Нет `Makefile`/`make build`, команда сборки не задокументирована. Системный Go — 1.18/1.22, а `go.mod` требует 1.25.0/1.25.5/1.26 (нужен `GOTOOLCHAIN`, тулчейны скачиваются; кэш есть).
* Вложенные `.git` в `lib/connect` (12 МБ!), `lib/proxy`, `lib/sdk` и др. — `git status` шумит, можно случайно закоммитить чужой репозиторий.
* `lib/connect/res/images` — 11 МБ скриншотов в библиотеке; итого `lib/connect` 26 МБ.
* 66 267 строк / 2.3 МБ сгенерированного `ip_security.go` компилируются в каждый бинарь (31–36 МБ) и замедляют сборку.
* Нет CI (gofmt/vet/staticcheck/shellcheck/pytest), нет README на приложение, нет `.editorconfig`.
* Тесты не покрывают: скрипт (только «Credentials not found»), GUI-цикл остановки, TUN-маршруты (нужен netns/user-namespace), SOCKS5 end-to-end.
* `.pytest_cache`, `__pycache__` в рабочем дереве (частично в `.gitignore` нет).

---

## 5. Предложения

Приоритеты: **P0** — безопасность и «приложение не должно вредить», **P1** — стабильность и предсказуемость, **P2** — архитектура и техдолг. Оценки трудоёмкости — в человеко-днях для одного разработчика.

### 5.1 Быстрые победы (≤ 1 часа каждая, максимальный эффект)

**1) Убрать печать пакетов из горячего пути.** Добавить флаг `--verbose` (или `-v`) и счётчики:

```go
var (
    verbose    = cli.BoolFlag{Name: "verbose", Usage: "log every packet (slow)"}
    pktIn, pktOut atomic.Uint64
    pktDrop       atomic.Uint64
)
...
if cfg.verbose {
    fmt.Printf("[TUN>>] %s %s->%s (%d bytes)\n", proto, srcIP, dstIP, len(packet))
}
pktOut.Add(1)
```

Периодически (раз в 10 с) печатать одну строку статистики: pps, bytes, drops. То же для callback (`main.go:302`) и `socks/main.go:244` (там лог per-connection — оставить, но в файл/`-v`, а не в stdout).

**Ожидаемый эффект:** исчезают зависания и 1.6 ГБ логов; снимается основной источник деградации.

**2) Права на файлы и прокси-биндинг.**

```python
# gui/urnetwork-gui.py:276
fd = os.open(AUTH_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    ...
```

```go
// lib/proxy/socks/main.go:59
Value: "127.0.0.1:9999",
```
+ предупреждение, если `--addr` не loopback. В `urnetwork-connect.sh` — `umask 077` для `CONFIG_DIR` и `chmod 600` на `diagnostic.log`.

**3) Убрать креды из argv.** В скрипте убрать `--user-auth`/`--password` (бинарь уже читает env):

```bash
USER_AUTH="$USER_AUTH" PASSWORD="$PASSWORD" \
    ${TIMEOUT_CMD:-} "$bin" "$@" &
```
Для socks-режима процесс не root, но env всё равно читается только тем же пользователем.

**4) Ротация и фильтрация `diagnostic.log`.** Писать в файл только то, что нужно (не per-packet), ограничить размер (например, 10 МБ с `.1`), права 0600. Это же лечит рост файла до гигабайт.

**5) Остановка GUI без «залипаний»:** `start_new_session=True` вместо `preexec_fn`; `_poll_stop` с эскалацией через 5 с:

```python
def _poll_stop(self):
    if not self.process:
        self._finish_stop(); return False
    if self.process.poll() is not None:
        self.process = None; self._finish_stop(); return False
    self._stop_ticks += 1
    if self._stop_ticks == 25:   # 5 s
        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
    return True
```
и вызвать `off` в отдельном потоке (`threading.Thread(..., daemon=True).start()`), а не в GTK-колбэке.

**6) `on_process_end` → cleanup.** Если процесс завершился сам — всё равно выполнить `off` (в фоне) и показать «Tunnel exited unexpectedly (code N)».

**7) Не убивать чужой интерфейс.** Вместо `ip link delete` — проверка и внятная ошибка:

```go
if out, _ := exec.Command("ip", "link", "show", name).CombinedOutput(); len(out) > 0 {
    return nil, fmt.Errorf("interface %q already exists: stop the other instance first "+
        "(urnetwork-connect.sh off) or use --tun-name", name)
}
```
Плюс лок-файл (`flock`) в `urnetwork-connect.sh` и PID-файл с проверкой «тот ли это процесс».

**8) IPv6-«чёрная дыра» вместо утечки.** Пока нет dual-stack — на время соединения:

```go
runCmd("ip", "-6", "route", "add", "blackhole", "default", "metric", "10")
// teardown: route del blackhole default metric 10
```
Лучше: `--block-ipv6` по умолчанию включён для VPN-режима, с предупреждением; полноценный dual-stack — отдельная задача (нужен IPv6 в gVisor-стеке и в TUN, `lib/proxy/tun.go:73-79`).

**9) Единый источник MTU.** `setupRouting(iface, prefix, bypass, mtu)` → `ip link set <if> mtu <mtu> up`; `Read` буфер = `mtu+4` (сейчас 1400 хардкод при `--mtu` из флагов `main.go:694`; при `--mtu 1200` буфер 1264 < MTU 1400 → тихое обрезание пакетов `unix.Read`'ом).

### 5.2 Data path (P0/P1, ~2–3 дня)

**1) Разделить чтение и отправку.** Bounded-очередь + воркеры + счётчики дропов:

```go
sendCh := make(chan []byte, 2048)
go func() {
    for p := range sendCh {
        if !mc.SendPacket(source, protocol.ProvideMode_Network, p, 15*time.Second) {
            pktDrop.Add(1)
        }
    }
}()
for {
    p, err := tun.Read()
    if err != nil { ... }
    select {
    case sendCh <- p:
    default:
        pktDrop.Add(1)   // better drop than block the TUN read
    }
}
```
Дать возможность нескольким отправителям (`--send-workers`, по умолчанию 2–4), если библиотека допускает конкурентный `SendPacket`. Дедлайн 15 с уменьшить или сделать настраиваемым (15 с — это «завис навсегда» с точки зрения пакета).

**2) Убрать лишние копии.** Читать в переиспользуемый буфер, копировать один раз; или брать буфер из `connect.MessagePoolGet` и передавать владение в `SendPacket` (проверить контракт владения в `lib/connect`, иначе — двойное освобождение). В gVisor-стеке (socks) увеличить `ChannelSize` с 32 до 512–1024 и `WriteTimeout` до 100–200 мс, а пакет копировать в `PacketBuffer` без промежуточной копии (`buffer.MakeWithData` уже копирует — можно `buffer.MakeWithData` из пула).

**3) Диагностика потерь.** Счётчики: прочитано/отправлено/дропнуто/ошибок по направлениям, размер очереди, `SendPacket` latency p50/p99. Отдавать в `diagnostic.log` раз в 10 с и в файл статуса для GUI (см. п. 5.5).

**4) Проверить и зафиксировать результат** теми же измерениями из п. 2.2 (1 и 4 потока; ожидаем рост агрегата и устранение деградации при параллельных загрузках).

### 5.3 Lifecycle, маршруты, реконнект (P1, ~3 дня)

**1) Журнал изменений маршрутов.** Перед изменением сохранять состояние (`ip route show default`, старые маршруты для bypass-CIDR) в `~/.config/urnetwork/route-journal.json`; teardown — восстанавливать по журналу, а не «удалить по шаблону». Это делает `off` идемпотентным и безопасным при аварии (следующий запуск сначала «докатывает» журнал).

**2) Реконнект/health-check.** В Go-бинаре: если за N секунд не было входящих пакетов при активном исходящем трафике — пересоздать генератор (переавторизация, новый провайдер), экспоненциальный backoff. В `urnetwork-connect.sh` — цикл супервизора: перезапуск при `exit != 0` с backoff (сейчас retry не работает для крашей из-за `set -e` + `wait`, `:75,86`), флаг `--reconnect N`.

**3) Статус из реального состояния, а не из stdout.** Go-бинарь пишет `state.json` (connected, provider, egress IP, rx/tx, drops, mtu, routes) атомарно; GUI/скрипт читают его. Это убирает парсинг stdout и даёт основу для трея/статистики.

**4) `--no-route`.** Проверено: при `--no-route` (`main.go:266`) `setupRouting` не вызывается вообще, а адрес/`up`/MTU настраиваются только внутри него (`main.go:691-699`) — т.е. TUN остаётся без адреса и в состоянии DOWN, и приложение не может работать. Либо ставить `addr`+`up` в `createTun`, либо переименовать флаг в `--no-default-route` и пропускать только default-маршрут.

### 5.4 Безопасность (P0, ~2 дня)

**1) Убрать `install-nopasswd.sh` из рекомендаций.** Правильная схема — systemd-юнит от root + управление через polkit:

```ini
# /etc/systemd/system/urnetwork-vpn.service
[Unit]
Description=URnetwork VPN (TUN)
After=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/urnetwork/auth.env                 # 0600, root:root
ExecStart=/usr/local/libexec/build/urnetwork-vpn --verbose=false
Restart=on-failure
RestartSec=3
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
```
GUI/CLI: `systemctl start/stop urnetwork-vpn` (однократная polkit-авторизация, дальше без пароля по правилу polkit); либо `pkexec` для разового запуска. Никакого NOPASSWD на `ip` и на user-writable файлы. Если всё же нужен sudoers — только узкий helper (`/usr/local/sbin/urnetwork-vpn-helper` root-owned, `chmod 755`, `chown root`), принимающий ограниченный набор действий.

**2) Секреты.** Пароль — в keyring (`secret-tool`/`libsecret`) или в `auth.env` 0600; передавать только через env/stdin, никогда через argv. Добавить `--password-stdin`. В скрипте писать/читать `KEY=VALUE` без `source` (парсить и `export` явно), значения квотировать `shlex.quote`.

**3) SOCKS5: только loopback + опциональная аутентификация.** `127.0.0.1:9999` по умолчанию; для не-loopback адреса — требовать `--user/--pass` и печатать предупреждение; добавить ACL/host-allowlist через `ruleset` (`go-socks5` это умеет).

**4) Логи 0600** (`diagnostic.log`, `launcher.log`), в них не писать email/пароль (сейчас email попадает).

**5) Проверять JWT по подписи** (JWKS/ключ сервера) и починить `network_id`:

```go
// lib/connect/jwt.go:33
if networkIdStr, ok := claims["network_id"]; ok { ... }
```
Клиенту брать `client_id` из ответа `auth-client` (поле в JSON), а не из JWT.

### 5.5 GUI: качественный апгрейд (P1, ~3–4 дня)

1. **Неблокирующее чтение stdout**: `os.set_blocking(fd, False)` + `os.read(fd, 65536)` в `io_add_watch`, разбиение на строки в буфере; текст в `Gtk.TextView` — батчами через `GLib.idle_add` (например, раз в 100 мс), а не по строке.
2. **Правильный стоп**: `SIGTERM` → 5 с → `SIGKILL`; `off` — в фоне; `on_destroy` — сначала остановка, потом выход (`GLib.timeout_add` до завершения, или синхронный `wait` с таймаутом при закрытии).
3. **Единый владелец состояния**: читать `state.json`/`status --json` вместо парсинга stdout; статус «Connected» — только по реальному состоянию.
4. **Кнопки прокси** — активны только в socks-режиме, сбрасываются при выходе; состояние прокси показывать явно.
5. **Полезные метрики**: egress IP, текущий провайдер/локация, rx/tx, RTT до выхода (ICMP/ping через туннель), время сессии. Это же — ответ на частый вопрос «VPN работает?».
6. **Single instance** (`flock` на `~/.config/urnetwork/gui.lock`), опционально трей (AppIndicator) и автозапуск.
7. **Валидация**: не стартовать без локации (сейчас стартует и падает, `:411-413`), показывать текст ошибки от скрипта, а не только «failed to connect».
8. Убрать неиспользуемые `NETWORK_NAME`/`regions` или реализовать их.

### 5.6 Библиотека `lib/connect` (P2, ~1 неделя)

1. **Убрать `panic` из парсеров пакетов** (`lib/connect/ip_packet.go`): возвращать `error`, на уровне приёма — дроп + счётчик. Это единственная реальная удалённая DoS-поверхность, найденная в клиенте.
2. **JWT**: проверка подписи + исправление claim `network_id` (см. 5.4.5).
3. **TLS 1.3** (`net_tls.go:43`).
4. **STUN**: список из конфига/флага, дефолт — без сторонних серверов (или хотя бы документировать, что они используются).
5. **P2P и маршруты**: `SO_BINDTODEVICE`/`SO_MARK` для control/transport сокетов + policy routing, вместо ad-hoc bypass-маршрутов до двух хостов. Это убирает класс проблем «IP сменился → туннель умер» и рекурсию при новых соединениях к пирам.
6. **Аллокации/pooling**: пересмотреть `ChannelSize`/`WriteTimeout`, вернуть `MessagePool` в путь TUN (сейчас `lib/proxy/tun.go:290` закомментирован `MessagePoolReturn`), довести reuse до >99%.
7. **`ip_security.go`**: генератор (скрипт) + `go:embed` сжатого списка + парсинг при старте; убрать 66k строк из истории/ревью и уменьшить бинарь. Плюс refresh-процедура.
8. **Диагностика**: pps/drops/latency из multi-client в метрики (Prometheus уже есть в зависимостях `lib/proxy`).

### 5.7 Сборка, тесты, CI (P2, ~2 дня)

1. `Makefile`:

```make
GO ?= GOTOOLCHAIN=go1.26.0 /usr/local/go/bin/go
build:
	$(GO) build -trimpath -ldflags "-s -w" -o build/socksproxy ./lib/proxy/socks
	$(GO) build -trimpath -ldflags "-s -w" -o build/urnetwork-vpn ./cmd/urnetwork-vpn
test:
	$(GO) test ./cmd/... ./lib/proxy/...
	python3 -m pytest tests -q
lint:
	$(GO) vet ./...
	shellcheck scripts/*.sh
	ruff check gui tests 2>/dev/null || true
```
2. `toolchain go1.26.0` в `cmd/urnetwork-vpn/go.mod` (и `go 1.25.0` в остальных), чтобы сборка была воспроизводимой.
3. CI (GitHub Actions): gofmt/vet/test, shellcheck, pytest, сборка бинарей; артефакты — в релиз.
4. Тесты, которые сейчас отсутствуют:
   * E2E TUN без root: `unshare -rn` (user+net namespace) → запуск `urnetwork-vpn --no-route --tun-name test0`, проверка, что интерфейс создан/адрес назначен/маршруты добавлены/удалены; в CI — skip при недоступности.
   * golden-тесты `setupRouting`/`teardownRouting`/`selectBypassCIDRs` (последний уже есть).
   * pytest: остановка GUI (SIGKILL-эскалация), ротация лога, запись `auth` с 0600, `on_process_end` вызывает cleanup.
   * smoke SOCKS5: запуск с мок-сервером (`--api-url` на локальный httptest) — проверить логин/выбор/`listening`, без реальной сети.
5. Убрать вложенные `.git` в `lib/*` (сделать submodules или вендорить без `.git`), `res/images` — вынести из модуля, добавить `.gitignore` для `__pycache__`, `.pytest_cache`.

### 5.8 Документация (P2, ~0.5 дня)

`README.md` в корне: что это, как собрать (`make build`), как настроить (`register`/`auth`), как запускать (GUI/CLI/`systemctl`), какие режимы и ограничения (IPv6, только TCP+UDP через socks, где логи, как диагностировать). Ссылки на `docs/code-review.md` и `docs/refactoring-plan.md`.

---

## 6. План внедрения

| Итерация | Что | Трудоёмкость | Критерий готовности |
|---|---|---|---|
| **1. «Не вредить»** | 5.1 целиком (логи/флаг, права, креды, ротация, GUI-стоп, `on_process_end`, проверка интерфейса, IPv6 blackhole, MTU) + 5.4.1–5.4.3 (sudoers→systemd/polkit, секреты, loopback) | 2–3 дня | `ps` не показывает пароль; `auth`/логи 0600; socks слушает только 127.0.0.1; нет per-packet печати; GUI закрывается без осиротевших процессов; `curl -6` не утекает мимо туннеля |
| **2. «Стабильно и быстро»** | 5.2 (очередь/воркеры, буферы, счётчики), 5.3 (route-journal, реконнект, `state.json`, `--no-route`), 5.5 полностью | 1–1.5 недели | 4 потока через socks ≥ 2× текущего агрегата; после `kill -9` следующий запуск стартует с чистым состоянием; GUI показывает egress IP/rx-tx; «Connected» только по факту |
| **3. «Долг»** | 5.6 (panics→errors, JWT, TLS1.3, STUN, SO_BINDTODEVICE, блоклист, pooling), 5.7 (Makefile, CI, тесты), 5.8 (README) | 1.5–2 недели | `go vet`/CI зелёные; бинарь меньше; есть E2E-тест TUN; README описывает сборку и ограничения |

Что делать в первую очередь, если время ограничено: **5.1.1 + 5.1.2 + 5.1.7** (per-packet лог, права/креды, loopback) и **5.1.5** (удаление чужого TUN) — это ~полдня и снимает большинство наблюдаемых симптомов (зависания, 1.6 ГБ логов, «bad address», открытый прокси, утечку пароля).

---

## 7. Как проверить текущее состояние (выполнено)

```bash
# сборка (системный Go 1.18 не подойдёт; в кэше есть тулчейны 1.25.0/1.25.5/1.26.0)
cd cmd/urnetwork-vpn
GOTOOLCHAIN=go1.26.0 GOCACHE=/tmp/urnetwork-go-cache go build ./...   # OK
GOTOOLCHAIN=go1.26.0 GOCACHE=/tmp/urnetwork-go-cache go test ./...    # OK

# тесты обвязки
cd ../..
python3 -m pytest tests -q                                            # 13 passed

# живой смоук socks-режима (нужны креды в ~/.config/urnetwork/auth)
./build/socksproxy &                     # "socks5 server is listening on :9999"
curl --socks5-hostname 127.0.0.1:9999 https://api.ipify.org
# 67.85.246.40 — трафик идёт через сеть (напрямую было 91.78.35.192)
```

После патчей те же команды + измерения из п. 2.2 должны показать: отсутствие per-packet строк в логе, `ss -tln | grep 9999` → `127.0.0.1:9999`, `stat -c %a ~/.config/urnetwork/auth` → `600`, `curl -6` неприменим/блокирован, агрегат 4 потоков вырос.

---

## Приложение A. Сводная таблица «симптом → причина → строка»

| Симптом | Причина | Место |
|---|---|---|
| VPN «зависает», UI не отвечает, лог растёт | per-packet `fmt.Printf` + запись каждой строки в файл | `main.go:302,339`; `gui/urnetwork-gui.py:311` |
| Просадки скорости при нагрузке | синхронный `SendPacket(15s)` + канал 32 пакета | `main.go:341-346`; `tun.go:48,56` |
| `tun read error: bad address` | чужой инстанс удалил TUN | `main.go:540-541` |
| `Cannot find device "urnetwork"` | teardown после удаления интерфейса | `main.go:726-733`, `urnetwork-connect.sh:219` |
| Трафик IPv6 мимо VPN | нет v6-маршрута/адреса | `main.go:691-699`, `tun.go:73-79` |
| `RTNETLINK answers: File exists` | повторная настройка занятого интерфейса | `main.go:691,697` |
| `exit idle timeout` без восстановления | нет реконнекта/watchdog | `lib/connect/transfer.go:2206` |
| Пароль в `ps` | креды в argv | `urnetwork-connect.sh:193-202` |
| Пароль читаем с диска | запись без 0600 | `gui/urnetwork-gui.py:276-284` |
| Открытый прокси в LAN | bind `:9999` | `lib/proxy/socks/main.go:59` |
| Осиротевший процесс/маршруты после закрытия GUI | `on_destroy` без ожидания, `on_process_end` без cleanup | `gui/urnetwork-gui.py:489-498,552-558` |
| Passwordless root | NOPASSWD на user-writable скрипт и `ip` | `scripts/install-nopasswd.sh:4-9` |
| Удалённый DoS | `ParseIpPathWithPayload` читает `ipPacket[0]` без проверки длины (пустой пакет от пира); `ip_packet.go:20-32` — это внутренние OOS-билдеры, из сети не вызываются | `lib/connect/ip.go:2995` |
| Неверный `network_id` | claim `network_id` читается из `network_name` | `lib/connect/jwt.go:33` |
| Неподписанный JWT используется | `ParseUnverified` | `lib/connect/jwt.go:14-16`; `main.go:511-524` |

## Приложение B. Замечания по данным измерений

* Измерения скорости/латентности сделаны на одном exit-провайдере (US/NY) в один момент времени; абсолютные значения зависят от сети URnetwork и не являются «бенчмарком приложения». Значим именно **относительный** результат: деградация при параллельных потоках и рост задержки на запрос.
* Утверждение «узкое место — клиент» требует подтверждения после патчей 5.1.1/5.2 (сравнить те же 4 потока). Если после снятия per-packet лога и синхронной отправки агрегат вырастет кратно — гипотеза подтверждена; если нет — ограничение на стороне exit-пира/сети, и тогда приоритет — выбор провайдера и несколько пиров.
* `diagnostic.log` (1.6 ГБ) и `auth` (0664) проверены на диске; содержимое пароля не выводилось.
* После патчей сделан A/B (`lib/proxy/tun.go`: старые настройки 32/5 с против новых 512/1 с, один и тот же бинарь-сборщик, country US): прямые замеры в том же окне «плавали» от 0.49 до 2.18 МБ/с, socks — 131–806 КБ/с на обоих вариантах, exit-пир каждый раз разный. **Регрессии от новых настроек не видно, но и прироста в этих замерах нет**: доминирует качество exit-пира и общая деградация сети в момент теста. Поэтому гипотеза «узкое место — клиент» в отчёте оставлена как гипотеза, требующая повторных измерений в стабильной сети.

---

## 8. Что уже внедрено в этой итерации (после отчёта)

Все правки внесены в рабочее дерево (без коммитов), проверены сборкой и тестами.

### 8.1 VPN-ядро и маршрутизация — `cmd/urnetwork-vpn/main.go`

| Было | Стало |
|---|---|
| per-packet `fmt.Printf("[TUN>>] …")` и `[<<TUN]` в receive callback | печать только с `--verbose` (`URNETWORK_VERBOSE`), в обычном режиме одна строка статистики раз в 10 с |
| `SendPacket(..., 15s)` прямо в цикле чтения TUN | ограниченная очередь `--send-queue` (2048) + воркеры `--send-workers` (по умолчанию 1), неблокирующий `Enqueue`, счётчик `dropped=` |
| `make([]byte, mtu+64)` + копия на каждый пакет | один переиспользуемый буфер чтения; копия только на пакет, уходящий в очередь |
| `createTun` удалял существующий `urnetwork` | проверка `tunExists` + понятная ошибка `interface "urnetwork" already exists: … pass --replace-tun`; удаление только с `--replace-tun` |
| `ip link set … mtu 1400` жёстко | MTU берётся из `--mtu`, валидируется 576..9000 (`invalid mtu …`) |
| нет IPv6-маршрута → утечка | `--block-ipv6` (по умолчанию `true`) добавляет `ip -6 route add blackhole default metric 10`, teardown удаляет |
| `teardownRouting` вслепую удалял default-маршрут | `setupRouting` возвращает реально добавленные bypass-маршруты; teardown тихий и идемпотентный |
| нет состояния | `state.json` (`--state-file`/`URNETWORK_STATE_FILE`): mode, pid, provider, tun, mtu, ipv6_blocked, счётчики; пишется атомарно 0600 |
| сигнал → просто `return` | `cancel()` контекста + `sender.Stop()` + teardown |

Новые тесты: `TestValidateMtuRejectsOutOfRange`, `TestIPv6BlackholeArgs`,
`TestDescribePacket`, `TestSystemTunReadReturnsIndependentCopy`,
`TestPacketSenderEnqueuesDropsAndStops`, `TestWriteStateFileWritesJSONWithPrivatePerms`.
Все проходят: `ok github.com/user/urnetwork-vpn`.

### 8.2 SOCKS-прокси — `lib/proxy/socks/main.go`, `lib/proxy/tun.go`

- bind по умолчанию `127.0.0.1:9999`; при явном `0.0.0.0/::` печатается предупреждение
  (`socks5 listens on all interfaces … prefer 127.0.0.1:9999`), проверено live:
  `ss` показывает `127.0.0.1:9999`, в логе есть предупреждение для `0.0.0.0`.
- ошибка `listen` больше не игнорируется: `socks5 listen on %s failed: %w`.
- пустые пакеты и ошибки чтения TUN не отправляются в сеть (`read error:` не
  приводит к `SendPacket`).
- `Dialing …` печатается только с `--verbose`.
- буфер канала 512 (было 32), `WriteTimeout` 1 с, при dial-гонке адрес выбирается
  по кругу (`addrs[attempt%len(addrs)]`), а не случайно.

### 8.3 GUI — `gui/urnetwork-gui.py`

- `auth` пишется атомарно (`*.tmp` + `os.replace`) с правами `0600`, без кавычек,
  с проверкой на переводы строк.
- чтение stdout — неблокирующий fd (`os.set_blocking(fd, False)`) + построчный
  буфер в `_on_stdout_readable`; GTK-цикл больше не блокируется на `readline()`.
- glog-строки не пишутся на диск и не идут в UI, пока не включён «Verbose logs»
  (именно это давало `diagnostic.log` на 1.6 ГБ); статусные строки сохраняются;
  запись в лог батчится (`_queue_log`/`_flush_log`, 80 мс), файл ротируется на 10 МБ.
- stop: SIGTERM → ожидание 5 с → SIGKILL; `_finish_stop` асинхронный.
- при любом завершении движка (включая крах) запускается `off` в отдельном потоке —
  TUN и default-маршрут не остаются «висеть».
- один экземпляр GUI: `flock` на `gui.lock`; второй запуск печатает
  `URnetwork GUI is already running`.
- кнопки системного прокси неактивны вне SOCKS-режима; старт без выбранной
  локации запрещён с понятным сообщением.
- `URNETWORK_STATE_FILE` / `URNETWORK_VERBOSE` прокидываются в движок.

Тесты: `tests/test_gui_connection_status.py` (2) + новый
`tests/test_gui_hardening.py` (5: атомарная запись 0600, отказ на `\n`,
фильтрация glog, сохранение glog в verbose, «Connected» один раз). Итого
`python3 -m pytest tests -q` → **18 passed**.

### 8.4 Скрипты

`scripts/urnetwork-connect.sh`:
- креды только через окружение (`env USER_AUTH=… PASSWORD=…`) — в `ps` не видны;
  `auth` парсится, а не `source`-ится (файл — данные, не код);
- `flock` от двойного запуска (`Another urnetwork-connect instance is already running`);
- `run_with_retry` больше не выходит из `set -e` на `wait $pid`: падения реально
  перезапускаются (до `--retry`), коды 143/130 считаются штатной остановкой;
- перед стартом VPN: `save_default_route` + `cleanup_stale_vpn` (удаление
  осиротевшего устройства, восстановление маршрута, снятие IPv6-blackhole);
- в `off`/trap: SIGTERM → 3 с → SIGKILL для VPN, восстановление default-маршрута,
  снятие blackhole;
- `register` качает бинарник в `mktemp -d`, исправлен баг `tar A || tar B && cp`,
  `auth` пишется через `umask 077` + `chmod 600` + `mv`;
- `status` показывает аккаунт (без `source`), pid'ы, mode, `state.json`, наличие
  бинарников и предупреждение `default route points at missing urnetwork device (offline risk)`;
- `URNETWORK_CONFIG_DIR` стабилен через `sudo` re-exec; при запуске от root берётся
  конфиг `SUDO_USER`.

`scripts/install-nopasswd.sh` переписан: копирует launcher в
`/usr/local/libexec/urnetwork/urnetwork-connect.sh`, бинарники — в
`/usr/local/libexec/build/`, и выдаёт NOPASSWD **только на root-owned копию**.
Старые правила (`NOPASSWD` на скрипт в чекауте, `build/urnetwork-vpn`,
`/usr/sbin/ip`) удалены — они были эквивалентны passwordless root.

### 8.5 Библиотека `lib/connect`

- `ip.go`: `ParseIpPathWithPayload` проверяет длину (`empty ip packet`) и
  конвертирует панику парсера в ошибку (`bad ip packet: %v`) — устранён
  удалённо-триггерящийся crash на пустом пакете.
- `jwt.go`: `NetworkId` из `claims["network_id"]` (был copy-paste из
  `network_name`); все claim'ы читаются с `.(string)` без паники.
- `net_tls.go`: `MinVersion: tls.VersionTLS13` (live-проверка: и
  `api.bringyour.com`, и `connect.bringyour.com` отвечают TLS 1.3).

### 8.6 Сборка, документация, CI

- `Makefile`: `build`, `test` (Go + pytest + `bash -n`), `vet`, `fmt`, `install`,
  `install-desktop`, `clean`; `GOTOOLCHAIN=go1.26.0` и выбор `/usr/local/go/bin/go`,
  потому что `/usr/bin/go` 1.18 молча игнорирует `GOTOOLCHAIN`.
- `README.md`: запуск, конфиг, модель безопасности, что изменилось, ограничения.
- `.github/workflows/ci.yml`: gofmt, `make build`, Go-тесты клиента, `bash -n`,
  pytest.
- `.gitignore`: `__pycache__/`, `.pytest_cache/`, `*.pyc`.

### 8.7 Проверено

```text
make build          # build/urnetwork-vpn, build/socksproxy — OK (Nov 4)
make test           # go test ok; pytest 30 passed; bash -n всех скриптов ok
gofmt -l …          # пусто
urnetwork-vpn --help # новые флаги на месте; --mtu 100 → invalid mtu 100 …
socksproxy          # ss: 127.0.0.1:9999; лог: listening on 127.0.0.1:9999
socksproxy --addr 0.0.0.0:9999 # предупреждение про все интерфейсы
status              # Account, режимы, state.json, подсказки
```

Живой egress-тест после патчей: `curl --socks5-hostname 127.0.0.1:9999
https://api.ipify.org` → `76.86.254.69` при прямом `91.78.35.192`, то есть
трафик по-прежнему идёт через сеть.

Живой TUN-тест (17:34, после найденного и исправленного бага: lock брался до
`sudo`, а `sudo` наследовал fd с блокировкой — root-копия падала с «Another
instance is already running»; теперь lock берётся после sudo-перезапуска):

```text
sudo …/urnetwork-connect.sh vpn   → интерфейс urnetwork UP, inet 10.0.0.1/24, mtu 1400
ip route show default             → default dev urnetwork scope link src 10.0.0.1 metric 10
ip -6 route show                  → blackhole default dev lo metric 10   (IPv6-утечка закрыта)
state.json (root, 0600)           → provider/tun_name/tun_addr/mtu/started_at/счётчики
curl https://api.ipify.org        → 47.14.1.118  (прямой egress 91.78.35.192)
off                               → ✓ Stopped; линк удалён, default восстановлен из
                                    default-route.before, blackhole снят, pid/state/backup удалены
```

### 8.8 Пароль sudo в отдельном окне терминала

У GUI нет управляющего терминала, поэтому `sudo` не может спросить пароль
сам: он падал с «no tty present and no askpass program specified». Теперь GUI
передаёт движку `SUDO_ASKPASS=scripts/urnetwork-askpass.sh` (и
`SUDO_ASKPASS_REQUIRE=prefer`), а `urnetwork-connect.sh` при запуске без TTY
вызывает `sudo -A`. Если пароль нужен, помощник открывает **отдельное окно
терминала** (gnome-terminal/konsole/xfce4-terminal/…), читает пароль через
`read -s` и отдаёт его `sudo` через stdout. Пароль не попадает в `argv` и не
пишется на диск: файл-канал лежит в приватном каталоге `$XDG_RUNTIME_DIR` с
правами `0700` и удаляется сразу после чтения.

Почему не `sudo -v` в терминале: sudo 1.9.9 по умолчанию хранит timestamp
per-tty (`timestamp_type=tty`), поэтому кэш, полученный в окне терминала, не
виден процессу GUI. Askpass отдаёт пароль непосредственно тому `sudo`, который
запускает движок.

Дополнительно:
- `off` сначала проверяет, есть ли что убирать (pid-файлы, устройство
  `urnetwork`, blackhole-маршрут, backup маршрута); если нет — выходит с
  `✓ Nothing to stop` и не просит пароль (штатное завершение движка уже всё
  почистило через trap).
- `scripts/install-nopasswd.sh` теперь ставит и root-owned копию askpass
  (`/usr/local/libexec/urnetwork/urnetwork-askpass.sh`); GUI предпочитает
  копию из репозитория, а если её нет — установленную.
- тесты: `tests/test_askpass.py` (3: пароль возвращается, нет DISPLAY, нет
  терминала) и два новых теста в `tests/test_gui_hardening.py` (env с
  `SUDO_ASKPASS`, helper исполняемый). Итого `pytest` → **23 passed**.

Замечание по этой машине: в `/etc/sudoers.d/urnetwork` уже лежит правило из
прошлой версии (`NOPASSWD` на скрипт в чекауте, `build/urnetwork-vpn` и
`/usr/sbin/ip`) — оно эквивалентно passwordless root. Чтобы его заменить,
нужно один раз выполнить `sudo scripts/install-nopasswd.sh`; до этого момента
VPN в GUI будет запускаться без окна пароля, потому что старые правила всё ещё
разрешают запуск.

### 8.9 Пароль URnetwork в системном хранилище секретов

Раньше провайдерский пароль лежал в `~/.config/urnetwork/auth` (0600). Теперь
GUI хранит его в Secret Service (GNOME Keyring) через новый helper
`scripts/urnetwork-credentials.py` (подкоманды `available/get/set/delete`,
пароль передаётся через stdin, не через `argv`). В `auth` остаются только
`USER_AUTH`/`NETWORK_NAME`, а если keyring недоступен — GUI откатывается на
запись пароля в файл `0600` (как раньше). При первом запуске старый пароль из
файла автоматически переносится в keyring, после чего из файла удаляется.
Терминальный launcher при пустом `PASSWORD` тоже пробует keyring
(`urnetwork-connect.sh` → `urnetwork-credentials.py get`), поэтому CLI
продолжает работать без plaintext-пароля. Отключается через
`URNETWORK_KEYRING=0`.

Тесты: `tests/test_credentials_helper.py` (4, скипаются без Secret Service) и
три новых теста в `tests/test_gui_hardening.py` (сохранение без `PASSWORD=`,
загрузка из keyring, миграция из старого файла). Итого `pytest` → **30 passed**.

### 8.10 Пакет `.deb`

`make deb` (он же `scripts/build-deb.sh`) собирает
`dist/urnetwork-client_<версия>_<arch>.deb` без прав root: staging-дерево +
`dpkg-deb --root-owner-group -Zxz`. Внутри:

- `/usr/lib/urnetwork/{build,gui,scripts}` — бинарники, GUI и хелперы
  (askpass, credentials, launcher);
- `/usr/bin/{urnetwork-gui,urnetwork-connect,urnetwork-vpn}` — обёртки и
  симлинк на движок;
- desktop-entry, иконка, man-страницы `urnetwork-gui(1)`,
  `urnetwork-connect(1)`, `urnetwork-vpn(1)`, `copyright`/`changelog.Debian.gz`;
- `Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-secret-1, sudo,
  iproute2, curl, libc6 (>= 2.34)`.

Особенности: пути в GUI вычисляются от `__file__` (`../scripts/...`), поэтому
пакетный layout `/usr/lib/urnetwork/{gui,scripts}` работает без правок; в
`urnetwork-connect.sh` добавлены `find_credentials_helper()` и выбор
`INSTALLED_SCRIPT` из `/usr/lib/urnetwork/scripts` либо
`/usr/local/libexec/urnetwork`. Пакет **не** устанавливает правил sudoers:
пароль sudo спрашивается окном askpass при старте TUN-режима; правило
`scripts/install-nopasswd.sh` остаётся опциональным. Сборка: `-buildmode=pie`,
`-ldflags '-s -w'` — бинарники проходят lintian-проверки hardening, пакет
~14 МБ; `lintian` чист (единственное переопределение —
`initial-upload-closes-no-bugs`).

## 9. Что осталось (не входило в эту итерацию)

1. **Производительность**: измерить 4 параллельных потока в стабильной сети;
   если агрегат всё ещё упирается в ~0.4 МБ/с — добавлять несколько пиров
   (`send-workers` > 1 только после проверки, что порядок пакетов не важен для
   протокола) и смотреть `dropped=` в строке статистики.
2. **systemd-юнит + polkit** вместо sudo-обёртки (в отчёте 5.4.4): сейчас
   выбран более простой и уже безопасный вариант — root-owned копия launcher'а.
   systemd дал бы автозапуск и Watchdog, но требует отдельного дизайна
   (WorkingDirectory, User, CapabilityBoundingSet).
3. **`lib/sdk`**: свои TLS1.2-конфиги (`device_rpc_transport.go:459,482`) и
   `ParseUnverified` в `ClientAuth.ClientId` — вне клиента, но при следующей
   итерации стоит перевести на проверенный путь.
4. **Блоклист в `ip_security.go`** (66k строк, 2.3 МБ): сгенерировать в
   компактный формат (`go:embed` + бинарный поиск), net.IPNet/префиксы.
5. **`--no-route`** теперь работает, но GUI его не выставляет; также нет
   индикатора egress IP/трафика в GUI (есть `state.json` — можно показать).
6. Коммиты не делались: в репозитории остаются посторонние незакоммиченные
   изменения (`cmd/urnetwork-vpn/go.mod`, `gui/urnetwork-vpn.desktop`,
   `lib/proxy/go.sum`) и вложенные git-репозитории `lib/connect`, `lib/proxy`.
