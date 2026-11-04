#!/usr/bin/env bash
# Launch the URnetwork VPN GTK GUI from a desktop entry.
#
# Why this wrapper exists (instead of Exec=/usr/bin/python3 ...gui.py):
#   * a .desktop entry runs with Terminal=false, so any traceback is lost and the
#     launcher fails completely silently;
#   * "python3" on PATH here is linuxbrew 3.13, which has neither `gi` nor
#     PyGObject, while the GTK bindings live in /usr/bin/python3 (3.10);
#   * GUI apps need a stable cwd and a log we can inspect after a failed click.
#
# Exit codes: 0 ok, 1 interpreter/bindings missing, 2 GUI script missing,
#             3 GTK cannot open a display.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
GUI="$PROJECT_DIR/gui/urnetwork-gui.py"
LOG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/urnetwork"
LOG="$LOG_DIR/launcher.log"

mkdir -p "$LOG_DIR" 2>/dev/null || true

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true
}

notify_error() {
    local msg="$1"
    if command -v notify-send >/dev/null 2>&1; then
        notify-send -a "URnetwork VPN" -i dialog-error "URnetwork VPN" "$msg" 2>/dev/null
    elif command -v zenity >/dev/null 2>&1; then
        # Detach: a blocking dialog would keep this wrapper alive after the GUI
        # is gone, and would pile up one process per failed attempt.
        zenity --error --title="URnetwork VPN" --no-wrap --text="$msg" \
            >/dev/null 2>&1 &
        disown 2>/dev/null || true
    fi
}

fail() {
    local code="$1"; shift
    log "ERROR: $*"
    notify_error "$*
Подробности: $LOG"
    exit "$code"
}

# Rotate the launcher log so it cannot grow without bound.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 524288 ]; then
    mv -f "$LOG" "$LOG.1" 2>/dev/null || true
fi

log "=== launch requested (cwd=$PWD pid=$$) ==="

[ -f "$GUI" ] || fail 2 "Не найден файл GUI: $GUI"

# Pick the first interpreter that can actually import GTK bindings. Never trust
# plain "python3": it may resolve to a Homebrew/venv interpreter without gi.
PY=""
for candidate in \
    "${URNETWORK_PYTHON:-}" \
    /usr/bin/python3 \
    /usr/bin/python3.10 \
    /usr/bin/python3.12 \
    /usr/bin/python3.11 \
    python3
do
    [ -n "$candidate" ] || continue
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import gi' >/dev/null 2>&1; then
        PY="$candidate"
        break
    fi
done

if [ -z "$PY" ]; then
    fail 1 "Не найден Python с GTK-биндингами (python3-gi).
Установите пакет python3-gi."
fi

log "using interpreter: $PY"

# GDK aborts with an unclear message when there is no usable display; report it.
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    fail 3 "Нет графической сессии (DISPLAY/WAYLAND_DISPLAY не заданы)."
fi

cd "$PROJECT_DIR" || fail 2 "Не удалось перейти в $PROJECT_DIR"

log "exec: $PY $GUI $*"
"$PY" "$GUI" "$@" 2>>"$LOG"
status=$?
log "=== GUI exited with status $status ==="

# Report a real failure only. Exits >= 128 mean the process was killed by a
# signal (SIGTERM on logout, SIGKILL, ...) - that is not a crash and must not
# raise a dialog; Terminal=false would otherwise hide the whole story anyway.
if [ "$status" -ne 0 ] && [ "$status" -lt 128 ]; then
    notify_error "GUI завершился с ошибкой (код $status).
Подробности: $LOG"
fi
exit "$status"
