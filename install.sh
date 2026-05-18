#!/usr/bin/env bash
# install.sh — idempotent setup for voice-opencode
#
# Re-runnable. Detects what's already there and only does what's missing.
# Asks for sudo via $SUDO_ASKPASS (ksshaskpass on this box).
#
# Steps:
#   1. OS / display-server / desktop detection
#   2. pacman packages (selected per environment)
#   3. Python venv + pip deps
#   4. whisper.cpp model
#   5. Default Piper voice
#   6. systemd --user services (opencode-serve, ydotool)
#   7. .desktop launcher in app menu
#   8. Hyprland binds + tray autostart (only if Hyprland)
#   9. opencode MCP integration (voice_desktop server)
#
# Currently supported: Arch / CachyOS Linux with Hyprland or any
# X11/Wayland DE. macOS and Windows are out of scope (the project
# is Linux-only on purpose — see AGENTS.md).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DEV_MODE=0
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=1 ;;
        -h|--help)
            sed -n '2,20p' "$0"; exit 0 ;;
    esac
done

# ---------- helpers ----------------------------------------------------------
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m  ✗\033[0m %s\n' "$*" >&2; }

need_sudo() {
    if [[ -n "${SUDO_ASKPASS:-}" ]]; then
        sudo -A "$@"
    elif [[ -x /usr/bin/ksshaskpass ]]; then
        SUDO_ASKPASS=/usr/bin/ksshaskpass sudo -A "$@"
    else
        sudo "$@"
    fi
}

ensure_line() {
    # ensure_line <file> <line>
    local file="$1" line="$2"
    mkdir -p "$(dirname "$file")"
    touch "$file"
    grep -qxF "$line" "$file" || printf '%s\n' "$line" >> "$file"
}

# ---------- 1. environment detection ----------------------------------------
log "Detecting environment…"

case "$(uname -s)" in
    Linux)  OS_KIND=linux ;;
    Darwin) err "macOS is not supported (Linux-only project). See AGENTS.md."; exit 1 ;;
    MINGW*|MSYS*|CYGWIN*)
            err "Windows is not supported (Linux-only project). See AGENTS.md."; exit 1 ;;
    *)      err "Unknown OS: $(uname -s)"; exit 1 ;;
esac

if ! command -v pacman >/dev/null 2>&1; then
    err "pacman not found. Only Arch / CachyOS are supported right now."
    err "(Phase: add Debian / Fedora installers — PRs welcome.)"
    exit 1
fi

# Display server: Wayland vs X11.
DISPLAY_KIND="${XDG_SESSION_TYPE:-}"
if [[ -z "$DISPLAY_KIND" ]]; then
    [[ -n "${WAYLAND_DISPLAY:-}" ]] && DISPLAY_KIND=wayland
    [[ -z "$DISPLAY_KIND" && -n "${DISPLAY:-}" ]] && DISPLAY_KIND=x11
fi
DISPLAY_KIND="${DISPLAY_KIND:-unknown}"

# Compositor / DE hints (used to pick the dialog backend).
WM_HINT="${XDG_CURRENT_DESKTOP:-}${XDG_SESSION_DESKTOP:-}"
IS_HYPRLAND=0
IS_KDE=0
[[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && IS_HYPRLAND=1
[[ "$WM_HINT" =~ (KDE|kde|plasma|Plasma) ]] && IS_KDE=1

ok "OS=$OS_KIND  display=$DISPLAY_KIND  hyprland=$IS_HYPRLAND  kde=$IS_KDE"

# ---------- 2. pacman packages ----------------------------------------------
# Core — needed on any Linux box.
PACMAN_PKGS=(
    sox                       # audio utilities
    whisper.cpp               # local STT (whisper-cli)
    piper-tts-bin             # local TTS (piper-tts)
    ydotool                   # synthetic mouse + keys via uinput
    libnotify                 # notify-send
    alsa-utils                # arecord
    python                    # for venv
    wireplumber               # wpctl for audio control (Phase H)
    playerctl                 # MPRIS media transport (Phase H)
    gtk3                      # gtk-launch for XDG apps (Phase I)
    tesseract                 # OCR engine (Phase F)
    tesseract-data-eng        # English language data
    tesseract-data-spa        # Spanish language data
)

# Display-server specific.
if [[ "$DISPLAY_KIND" == "wayland" ]]; then
    PACMAN_PKGS+=(
        grim                  # Wayland screenshots
        wtype                 # Wayland virtual keyboard
        wl-clipboard          # wl-copy / wl-paste (Phase B)
    )
else
    # X11 (or unknown — install both so anything works).
    PACMAN_PKGS+=(
        xclip                 # X11 clipboard (Phase B)
        scrot                 # X11 screenshot fallback (future)
        xdotool               # X11 input fallback (future)
    )
fi

# Dialog backend: kdialog if KDE, otherwise zenity.
# (Both are cheap; user can install the other manually if they want
# to test the fallback path.)
if (( IS_KDE )); then
    PACMAN_PKGS+=( kdialog )
else
    PACMAN_PKGS+=( zenity )
fi

log "Checking pacman packages…"
MISSING=()
for p in "${PACMAN_PKGS[@]}"; do
    pacman -Qq "$p" >/dev/null 2>&1 || MISSING+=("$p")
done
if (( ${#MISSING[@]} > 0 )); then
    log "Installing missing packages: ${MISSING[*]}"
    need_sudo pacman -S --needed --noconfirm "${MISSING[@]}"
else
    ok "All pacman packages present."
fi

# ---------- 3. Python venv + pip deps ---------------------------------------
log "Setting up Python venv…"
if [[ ! -x venv/bin/python ]]; then
    python -m venv venv
    ok "venv created."
else
    ok "venv already exists."
fi
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet requests PyQt6 mcp
ok "runtime deps installed (requests, PyQt6, mcp)."

# Install the package itself in editable mode so `python -m voice_opencode` works
# without needing the wrapper's PYTHONPATH=src trick when imported elsewhere.
if [[ -f pyproject.toml ]]; then
    ./venv/bin/pip install --quiet -e . >/dev/null 2>&1 || \
        warn "editable install failed; the wrapper still works via PYTHONPATH=src"
    ok "voice_opencode installed (editable)."
fi

if (( DEV_MODE )); then
    ./venv/bin/pip install --quiet ruff mypy pytest
    ok "dev deps installed (ruff, mypy, pytest)."
fi

# ---------- 4. whisper model -------------------------------------------------
WHISPER_MODEL="${WHISPER_MODEL:-ggml-small.bin}"
WHISPER_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/${WHISPER_MODEL}"
log "Checking whisper model (${WHISPER_MODEL})…"
mkdir -p models
if [[ -s "models/${WHISPER_MODEL}" ]]; then
    ok "Model already present ($(du -h "models/${WHISPER_MODEL}" | cut -f1))."
else
    log "Downloading model from ${WHISPER_URL}"
    curl -fL --progress-bar -o "models/${WHISPER_MODEL}" "$WHISPER_URL"
    ok "Model downloaded."
fi

# ---------- 5. Default Piper voice -------------------------------------------
DEFAULT_VOICE="${DEFAULT_VOICE:-es_AR-daniela-high}"
log "Checking Piper voices…"
mkdir -p voices
if compgen -G 'voices/*.onnx' > /dev/null; then
    ok "Voices already present: $(ls voices/*.onnx | wc -l)"
else
    log "No voices found. Downloading default: ${DEFAULT_VOICE}"
    # Map default voice → huggingface path
    case "$DEFAULT_VOICE" in
        es_AR-daniela-high) ./download-voice.sh es/es_AR/daniela/high ;;
        es_ES-sharvard-medium) ./download-voice.sh es/es_ES/sharvard/medium ;;
        *) err "Unknown default voice; download manually with download-voice.sh"; exit 1 ;;
    esac
fi

# ---------- 6. systemd user services ----------------------------------------
log "Setting up systemd --user services…"
SYSTEMD_USER="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_USER"

if [[ -f opencode-serve.service ]]; then
    cp -f opencode-serve.service "$SYSTEMD_USER/"
    systemctl --user daemon-reload
    systemctl --user enable --now opencode-serve.service >/dev/null 2>&1 || true
    ok "opencode-serve.service enabled."
fi

if [[ -f /usr/lib/systemd/user/ydotool.service ]]; then
    systemctl --user enable --now ydotool.service >/dev/null 2>&1 || true
    ok "ydotool.service enabled (user)."
    # Verify uinput access
    if [[ -r /dev/uinput && -w /dev/uinput ]]; then
        ok "/dev/uinput accessible."
    else
        warn "/dev/uinput not accessible. You may need: sudo usermod -aG input $USER && reboot"
    fi
fi

# ---------- 7. .desktop launcher --------------------------------------------
log "Installing .desktop launcher…"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/voice-opencode.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Voice OpenCode
GenericName=Voice assistant for opencode
Comment=Push-to-talk voice interface for opencode (F9)
Exec=$ROOT/voice tray
Icon=$ROOT/icons/voice-idle.svg
Terminal=false
Categories=Utility;AudioVideo;Development;
StartupNotify=false
Keywords=voice;stt;tts;opencode;assistant;whisper;piper;
SingleMainWindow=true
EOF
update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
ok "voice-opencode.desktop installed."

# ---------- 8. Hyprland binds + autostart -----------------------------------
if (( IS_HYPRLAND )); then
    log "Wiring Hyprland binds + autostart…"
    HYPR_CONFD="$HOME/.config/hypr/conf.d"
    HYPR_VOICE="$HYPR_CONFD/voice.conf"
    HYPR_AUTO="$HYPR_CONFD/autostart.conf"

    if [[ ! -f "$HYPR_VOICE" ]]; then
        mkdir -p "$HYPR_CONFD"
        cat > "$HYPR_VOICE" <<EOF
# voice-opencode push-to-talk
bind  = , F9, exec, $ROOT/voice start
bindr = , F9, exec, $ROOT/voice stop
# Forget current opencode session
bind  = SUPER, F9, exec, $ROOT/voice reset
EOF
        ok "Created $HYPR_VOICE"
        warn "Make sure your hyprland.conf sources conf.d/*.conf"
    else
        ok "$HYPR_VOICE already exists."
    fi

    ensure_line "$HYPR_AUTO" "exec-once = $ROOT/voice tray"
    ok "Tray autostart present in $HYPR_AUTO"
else
    warn "Not Hyprland — skipping Hyprland binds."
    warn "Bind F9 manually in your DE:"
    warn "  press   → $ROOT/voice start"
    warn "  release → $ROOT/voice stop"
    warn "  Super+F9 → $ROOT/voice reset"
fi

# ---------- 9. opencode MCP integration -------------------------------------
log "Wiring opencode MCP integration…"
OC_CFG_DIR="$HOME/.config/opencode"
OC_CFG="$OC_CFG_DIR/opencode.json"
mkdir -p "$OC_CFG_DIR"

if [[ ! -f "$OC_CFG" ]]; then
    cat > "$OC_CFG" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "mcp": {
    "voice_desktop": {
      "type": "local",
      "command": ["$ROOT/voice", "mcp", "serve"],
      "enabled": true
    }
  }
}
EOF
    ok "Created $OC_CFG with voice_desktop MCP server."
elif ! grep -q '"voice_desktop"' "$OC_CFG"; then
    warn "$OC_CFG exists but doesn't reference voice_desktop."
    warn "Add this to its 'mcp' object manually:"
    warn '    "voice_desktop": {"type":"local","command":["'"$ROOT"'/voice","mcp","serve"],"enabled":true}'
else
    ok "voice_desktop already present in $OC_CFG"
fi

# ---------- done -------------------------------------------------------------
echo
log "Done. Quick check:"
./voice state || warn "Server may need a moment to boot. Try: systemctl --user status opencode-serve"
echo
ok "Reload Hyprland (super+shift+r or 'hyprctl reload') to pick up new binds."
ok "Launch the tray now with:  ./voice tray  &"

(( IS_HYPRLAND )) || ok "(Non-Hyprland: bind F9 in your DE — see warnings above.)"
