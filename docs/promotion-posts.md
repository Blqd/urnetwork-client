# Готовые тексты для анонсов

Ниже — тексты под конкретные площадки. Правила: один канал — один текст,
ссылка на репозиторий не в первой строке (кроме релиз-новостей), никаких
«обход блокировок» и слов «VPN для доступа к заблокированным сайтам».
Перед публикацией проверить фактическое состояние README и релизов.

Везде ниже подставьте актуальный тег (сейчас `v0.1.1`).

---

## 1. Show HN (Hacker News)

**Title**
```
Show HN: Native GTK3 URnetwork VPN client for Ubuntu (Go + Python, MPL-2.0)
```

**Text**
```
There are URnetwork clients for Linux, but as far as I can tell none of them
ship a desktop GUI or a proper .deb. So I built one around the upstream Go
libraries: the networking core stays Go (SOCKS5 and TUN modes, vendored
lib/connect, lib/sdk, lib/proxy), the shell is Python + PyGObject.

A few decisions that made it interesting to build:

- Privilege split: SOCKS5 mode needs no root at all and listens on
  127.0.0.1:9999. TUN mode needs root because it moves the default route into
  the `urnetwork` device; the launcher that runs as root is a root-owned copy
  in /usr/local/libexec, and the sudoers rule (opt-in) covers only that copy.
- Credential handling: the URnetwork password lives in GNOME Keyring via Secret
  Service. ~/.config/urnetwork/auth keeps only USER_AUTH/NETWORK_NAME. One
  exception is documented in SECURITY.md: the upstream `register` call passes
  the password in argv, so it is briefly visible in ps.
- Packaging: one .deb installs the GUI, the CLI launcher, man pages and a
  desktop entry. Releases are built by GitHub Actions on a v* tag.

Not trying to replace the official client — just to make it usable on an Ubuntu
desktop without a terminal. Feedback on the TUN teardown path (route restore
after suspend/resume, IPv6 leak blocking) would be especially useful.

Code and .deb: https://github.com/Blqd/urnetwork-client
```

---

## 2. r/Ubuntu

**Title**
```
I made a native GTK3 client for URnetwork with a one-package .deb (Ubuntu 22.04+)
```

**Text**
```
I wanted URnetwork on my Ubuntu desktop without keeping a terminal open, and the
existing Linux clients are CLI-only. So I wrote a GTK3 GUI (Python + PyGObject)
on top of the upstream Go networking stack.

What's in the box:
- `sudo dpkg -i urnetwork-client_*.deb` and you get `urnetwork-gui`, a menu
  entry, `urnetwork-connect status|socks|vpn|off` and man pages.
- SOCKS5 mode runs without root (127.0.0.1:9999).
- TUN mode routes everything through the `urnetwork` device and can block IPv6
  to avoid leaks.
- Password goes to GNOME Keyring, not to a plain file.

Dependencies are regular Ubuntu packages (python3-gi, gir1.2-gtk-3.0,
gir1.2-secret-1), no Snap/Flatpak required. It is MPL-2.0.

Screenshots and install instructions: https://github.com/Blqd/urnetwork-client

Happy to answer questions about the packaging or the privilege split.
```

---

## 3. r/linux

Правила сабреддита запрещают «drive-by» промо: пост должен быть техническим,
ссылка — в конце, и лучше отвечать в комментариях, а не рекламировать.

**Title**
```
Privilege separation in a Linux VPN client: SOCKS5 without root, TUN with a root-owned helper
```

**Text**
```
I maintain a small URnetwork client for Ubuntu and the part I keep coming back to
is the privilege boundary. Two modes:

1. SOCKS5 — a user process listening on 127.0.0.1:9999. No root, no routing
   changes, but applications have to opt in.
2. TUN — needs root: create the device, move the default route, restore it on
   exit. Putting the whole client behind sudo would mean a root process parsing
   user config, so instead the root part is a launcher copied into
   /usr/local/libexec (root-owned, not writable by the user). Credentials are
   handed over in a 0600 file owned by the user, read once and deleted; the
   config directory for the root side is derived from SUDO_USER rather than from
   an environment variable.

Two failure modes I had to design around: an interrupted `off` leaving the
default route pointing at a dead device, and a stale route journal being
replayed as root (the journal is mask-checked before use).

The GUI itself is GTK3/PyGObject and talks to the same scripts as the CLI, so
there is exactly one code path that touches routing.

Code: https://github.com/Blqd/urnetwork-client — curious how others handle the
"restore routes after suspend" problem.
```

---

## 4. r/selfhosted

**Title**
```
Open-source P2P VPN client for Linux desktops (GTK3 + .deb, SOCKS5/TUN modes)
```

**Text**
```
Not a self-hosted service, but it is the client side of a decentralized P2P
network and it is fully open source (MPL-2.0), so it may be useful to people
here who route traffic through their own setups.

- Native GTK3 GUI; the daemon/CLI is the same script the GUI calls.
- SOCKS5 mode for per-app proxying without root.
- TUN mode for system-wide routing with IPv6 leak blocking.
- Single .deb, no Electron, no Snap.

Repo: https://github.com/Blqd/urnetwork-client
```

---

## 5. r/privacy

**Title**
```
A Linux VPN client that keeps the password in the keyring and root out of the user config
```

**Text**
```
Sharing the threat model rather than the feature list, because that is the part
I actually want reviewed.

This is a URnetwork client for Ubuntu with a GTK3 GUI. Design choices:

- The URnetwork password is stored in GNOME Keyring (Secret Service). The config
  file keeps only the account identifier and network name, 0600.
- Root is required only for TUN mode. The root-side launcher is a root-owned
  copy; the sudoers rule (optional, not installed by the .deb) covers only that
  copy, never files in a user-writable checkout.
- In TUN mode IPv6 can be blackholed to avoid leaking around the tunnel.
- Known limitation, documented rather than hidden: the upstream `register`
  command passes the password in argv, so it is visible in ps during that single
  call.

SECURITY.md lists what counts as a vulnerability. I would appreciate a second
pair of eyes on the route-restore logic.

https://github.com/Blqd/urnetwork-client
```

---

## 6. Lobsters

**Title** (tags: `linux`, `privacy`, `show`)
```
URnetwork VPN client with a native GTK3 GUI for Ubuntu
```

**Text**
```
Existing URnetwork clients on Linux are CLI-only, so this adds a GTK3 front end
(Python/PyGObject) over the same Go networking core. Two modes: SOCKS5 without
root and TUN with a root-owned helper. Packaged as a single .deb, MPL-2.0,
releases built by CI on tag.

It also documents its own weak spots in SECURITY.md (one upstream call passes a
password via argv) — I would rather have that written down than discovered.
https://github.com/Blqd/urnetwork-client
```

---

## 7. Хабр

### Вариант А — хаб «Я пиарюсь» (однократно, коротко)

**Заголовок**
```
Сделал GTK3-клиент URnetwork для Ubuntu: Go-ядро, Python-GUI, один .deb
```

**Текст**
```
На Linux клиенты URnetwork существуют, но все, что я нашёл, — консольные.
Захотелось запускать сеть с рабочего стола: ввёл аккаунт, нажал Start.

Что внутри:
- сетевой стек — Go (вендоренные lib/connect, lib/sdk, lib/proxy), режимы
  SOCKS5 (без root, 127.0.0.1:9999) и TUN (нужен root, default route в
  устройство urnetwork, блокировка IPv6);
- GUI — Python + PyGObject: аккаунт, режим, локация выхода, журнал;
- привилегии разделены: root-часть — копия launcher'а в /usr/local/libexec,
  пароль — в GNOME Keyring;
- упаковка: один .deb (GUI, CLI, man, desktop entry), релизы собирает CI по тегу.

MPL-2.0. Инструкции и скриншоты: https://github.com/Blqd/urnetwork-client

Интересны отзывы по двум вещам: восстановление маршрутов после suspend/resume
и поведение на Ubuntu 24.04 с NetworkManager.
```

### Вариант Б — техническая статья (главный канал, без «пиара»)

**Заголовок**
```
Разделяем привилегии в сетевом клиенте: SOCKS5 без root, TUN — с root-owned helper
```

Структура (5–7 тыс. знаков):
1. Задача: десктопный клиент, которому иногда нужен root, но не всё время.
2. Почему `sudo ./client` — плохо: root-процесс читает пользовательский конфиг.
3. Решение: user-часть + root-owned копия launcher'а, передача кредов файлом
   0600 с одноразовым чтением, каталог конфига из `SUDO_USER`.
4. Восстановление маршрутов: журнал `default-route.before`, проверка маски,
   поведение при обрыве.
5. Что осталось слабым (пароль в `argv` при `register`, GUI на главном потоке).
6. Выводы и ссылка на код в конце.

Требование Хабра: текст написан человеком, без «сгенерировано нейросетью»,
без политики, без призывов скачать.

---

## 8. OpenNET / linux.org.ru — релиз-новость

**Заголовок**
```
urnetwork-client 0.1.1 — open-source клиент URnetwork с GTK3 GUI для Ubuntu
```

**Текст**
```
Вышла версия 0.1.1 клиента URnetwork для Ubuntu: Go-стек (SOCKS5 и TUN),
GTK3-GUI на Python, установка из одного .deb, MPL-2.0.

Изменения версии: <перечислить из CHANGELOG>.
Пакет: urnetwork-client_0.1.1_amd64.deb + отдельные бинарники и SHA256SUMS.
Требования: Ubuntu 22.04+, python3-gi, gir1.2-gtk-3.0, gir1.2-secret-1.

Репозиторий: https://github.com/Blqd/urnetwork-client
Релиз: https://github.com/Blqd/urnetwork-client/releases/tag/v0.1.1
```

---

## 9. Telegram-чаты (запрос тестеров, не реклама)

```
Привет! Ищу тестеров для open-source GTK3-клиента URnetwork под Ubuntu
(MPL-2.0, ставится одним .deb). Проверить нужно три вещи:
1) Ubuntu 24.04 + Wayland — открывается ли окно и работает ли askpass при sudo;
2) NetworkManager: после переподключения сети маршрут возвращается корректно?;
3) suspend/resume в TUN-режиме — не остаётся ли default route на мёртвом устройстве.
Если что-то сломается — приложите вывод `urnetwork-connect status` и версию ядра.
Репозиторий: https://github.com/Blqd/urnetwork-client
```

---

## 10. Release notes v0.1.2 (шаблон, английский)

```markdown
## urnetwork-client 0.1.2

P2P VPN client for Ubuntu with a native GTK3 GUI.

### Added
- ...

### Fixed
- ...

### Install (Ubuntu 22.04+)
```bash
gh release download -R Blqd/urnetwork-client --pattern '*.deb'
sudo dpkg -i urnetwork-client_*.deb && sudo apt-get -f install
urnetwork-gui
```

Full changelog: https://github.com/Blqd/urnetwork-client/blob/master/CHANGELOG.md
```

---

## 11. awesome-selfhosted — строка и PR

Строка (раздел VPN, формат как у соседей — проверить порядок и правила
`CONTRIBUTING.md` репозитория awesome-selfhosted):

```markdown
- [urnetwork-client](https://github.com/Blqd/urnetwork-client) - P2P VPN client for Ubuntu with a native GTK3 GUI, SOCKS5 and TUN modes, single `.deb` package. ([Source Code](https://github.com/Blqd/urnetwork-client)) `MPL-2.0` `Python/Go`
```

**PR description**
```
Adds urnetwork-client, a desktop client for the URnetwork P2P VPN network.

- Open source: MPL-2.0
- Platform: Ubuntu 22.04+ (Linux desktop), GUI + CLI
- Install: single .deb from GitHub Releases (no Snap/Flatpak requirement)
- Why it fits the list: the existing clients for this network are CLI-only, so
  this is the first desktop GUI option; the .deb makes it a one-step install.

Checklist from CONTRIBUTING.md: entry is alphabetical in its section, links are
to the source repository, license and language tags are present.
```

---

## 12. Порядок публикации (чтобы не сжечь каналы)

1. Social preview + homepage + EN release notes — **до** любого поста.
2. Show HN → через 4–6 часов Reddit `r/Ubuntu` (разные аудитории, но
   пересечение есть).
3. `r/linux` и Lobsters — на следующий день, с техническим текстом.
4. Хабр — после первой волны: там важна не скорость, а качество статьи.
5. OpenNET/LOR — после релиза с внятным CHANGELOG.
6. Telegram — в любой момент, это не охват, а фидбэк.
