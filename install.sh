#!/usr/bin/env bash
# install.sh — idempotent setup for voice-opencode
#
# Re-runnable. Detects what's already there and only does what's missing.
# Asks for sudo via $SUDO_ASKPASS (ksshaskpass on Arch/KDE).
#
# Steps:
#   1. OS / package-manager / display-server / desktop detection
#   2. Distro packages (pacman / apt / dnf)
#   3. Python venv + pip deps
#   4. whisper.cpp model
#   5. Default Piper voice
#   6. systemd --user services (opencode-serve, ydotool)
#   7. .desktop launcher in app menu (+ XDG autostart on non-Hyprland)
#   8. Compositor binds (Hyprland binds OR XFCE xfconf-query keybinds)
#   9. opencode MCP integration (voice_desktop server)
#
# Supported distros:
#   - Arch / CachyOS (pacman)             — full repo coverage
#   - Debian / Ubuntu / Linux Lite (apt)  — repos + vendor/ for piper-tts and whisper.cpp
#   - Fedora / RHEL (dnf)                 — repos + vendor/ for piper-tts and whisper.cpp
#
# Supported desktops:
#   - Hyprland               — F9 bind via ~/.config/hypr/conf.d/voice.conf
#   - XFCE / XFCE-on-X11     — F9 bind via xfconf-query (xfce4-keyboard-shortcuts)
#   - Anything else          — install.sh prints manual bind instructions

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DEV_MODE=0
for arg in "$@"; do
    case "$arg" in
        --dev) DEV_MODE=1 ;;
        -h|--help)
            sed -n '2,28p' "$0"; exit 0 ;;
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
            err "Windows is not supported by install.sh — see install.ps1 (Phase C)."; exit 1 ;;
    *)      err "Unknown OS: $(uname -s)"; exit 1 ;;
esac

# Package manager detection: first wins.
PKG_MGR=""
for mgr in pacman apt dnf; do
    if command -v "$mgr" >/dev/null 2>&1; then
        PKG_MGR="$mgr"
        break
    fi
done
if [[ -z "$PKG_MGR" ]]; then
    err "No supported package manager found (pacman/apt/dnf)."
    exit 1
fi

# Display server: Wayland vs X11.
DISPLAY_KIND="${XDG_SESSION_TYPE:-}"
if [[ -z "$DISPLAY_KIND" ]]; then
    [[ -n "${WAYLAND_DISPLAY:-}" ]] && DISPLAY_KIND=wayland
    [[ -z "$DISPLAY_KIND" && -n "${DISPLAY:-}" ]] && DISPLAY_KIND=x11
fi
DISPLAY_KIND="${DISPLAY_KIND:-unknown}"

# Compositor / DE hints (used to pick the dialog backend and the bind path).
WM_HINT="${XDG_CURRENT_DESKTOP:-}${XDG_SESSION_DESKTOP:-}"
IS_HYPRLAND=0
IS_KDE=0
IS_XFCE=0
[[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] && IS_HYPRLAND=1
[[ "$WM_HINT" =~ (KDE|kde|plasma|Plasma) ]] && IS_KDE=1
[[ "$WM_HINT" =~ (XFCE|xfce|Xfce) ]] && IS_XFCE=1

ok "OS=$OS_KIND  pkg=$PKG_MGR  display=$DISPLAY_KIND  hyprland=$IS_HYPRLAND  kde=$IS_KDE  xfce=$IS_XFCE"

# ---------- 2. distro packages ----------------------------------------------
# Naming differs across distros. The matrix below is intentionally
# expanded (not generated) so a reader can grep their distro and see
# exactly what gets installed. Packages that don't exist in a distro's
# repos are marked as VENDORED — fetched from GitHub releases in
# step 2b instead.
#
# Categories (Arch column is canonical):
#
#   audio    sox, alsa-utils
#   stt      whisper.cpp
#   tts      piper-tts-bin
#   input    ydotool                       (wtype on wayland only)
#   notify   libnotify
#   media    wireplumber, playerctl        (Phase H; pipewire on all)
#   xdg      gtk3                          (gtk-launch)
#   ocr      tesseract + spa+eng data      (Phase F)
#   python   python                        (the venv interpreter)
#   wayland  grim, wtype, wl-clipboard
#   x11      xclip, scrot, xdotool
#   dialog   kdialog (KDE) | zenity (else)
#   xfce     xfconf                        (xfconf-query for keybinds)

# Pick a dialog backend. The Python KdialogBackend is tried first
# regardless, so install kdialog if (a) we're on KDE, (b) we're on
# Hyprland/wlroots where kdialog works fine and avoids dragging in
# GTK for zenity, or (c) kdialog is already installed (don't churn).
# Otherwise fall back to zenity.
_dialog_choice() {
    if (( IS_KDE )) || (( IS_HYPRLAND )); then
        echo kdialog
    elif command -v kdialog >/dev/null 2>&1; then
        echo kdialog
    else
        echo zenity
    fi
}
DIALOG_PKG="$(_dialog_choice)"

declare -a PKGS=()
declare -a VENDORED=()

case "$PKG_MGR" in
    pacman)
        PKGS=(
            sox alsa-utils
            whisper.cpp
            piper-tts-bin
            ydotool
            libnotify
            wireplumber playerctl
            gtk3
            tesseract tesseract-data-eng tesseract-data-spa
            python
        )
        if [[ "$DISPLAY_KIND" == "wayland" ]]; then
            PKGS+=( grim wtype wl-clipboard )
        else
            PKGS+=( xclip scrot xdotool )
        fi
        PKGS+=( "$DIALOG_PKG" )
        (( IS_XFCE )) && PKGS+=( xfce4-settings )
        ;;
    apt)
        # Debian/Ubuntu/Linux Lite. whisper.cpp and piper-tts are
        # NOT in the official repos — they are vendored from GitHub
        # in step 2b.
        PKGS=(
            sox alsa-utils
            ydotool
            libnotify-bin
            wireplumber playerctl
            libgtk-3-bin
            tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa
            python3 python3-venv python3-pip
            curl
        )
        if [[ "$DISPLAY_KIND" == "wayland" ]]; then
            PKGS+=( grim wtype wl-clipboard )
        else
            PKGS+=( xclip scrot xdotool )
        fi
        PKGS+=( "$DIALOG_PKG" )
        (( IS_XFCE )) && PKGS+=( xfce4-settings )
        ;;
    dnf)
        # Fedora/RHEL. Same situation as apt for whisper/piper.
        PKGS=(
            sox alsa-utils
            ydotool
            libnotify
            wireplumber playerctl
            gtk3
            tesseract tesseract-langpack-eng tesseract-langpack-spa
            python3 python3-pip
            curl
        )
        if [[ "$DISPLAY_KIND" == "wayland" ]]; then
            PKGS+=( grim wtype wl-clipboard )
        else
            PKGS+=( xclip scrot xdotool )
        fi
        PKGS+=( "$DIALOG_PKG" )
        (( IS_XFCE )) && PKGS+=( xfce4-settings )
        ;;
esac

# Per-PM installed-query and install-cmd functions.
pkg_query_installed() {
    # Echoes 1 if installed, 0 otherwise.
    case "$PKG_MGR" in
        pacman) pacman -Qq "$1" >/dev/null 2>&1 && echo 1 || echo 0 ;;
        apt)    dpkg -s "$1" >/dev/null 2>&1     && echo 1 || echo 0 ;;
        dnf)    rpm -q "$1" >/dev/null 2>&1      && echo 1 || echo 0 ;;
    esac
}

pkg_install() {
    case "$PKG_MGR" in
        pacman) need_sudo pacman -S --needed --noconfirm "$@" ;;
        apt)    need_sudo apt-get update -qq
                need_sudo apt-get install -y --no-install-recommends "$@" ;;
        dnf)    need_sudo dnf install -y "$@" ;;
    esac
}

log "Checking distro packages ($PKG_MGR)…"
MISSING=()
for p in "${PKGS[@]}"; do
    [[ "$(pkg_query_installed "$p")" == "0" ]] && MISSING+=("$p")
done
if (( ${#MISSING[@]} > 0 )); then
    log "Installing missing packages: ${MISSING[*]}"
    pkg_install "${MISSING[@]}"
else
    ok "All distro packages present."
fi

# ---------- 2b. vendored binaries (apt/dnf only) ----------------------------
# whisper.cpp and piper-tts are not packaged on Debian/Ubuntu/Fedora.
# We fetch the official GitHub release binaries into vendor/ and put
# them on PATH via the wrapper script. Idempotent — skip if already
# present.
VENDOR_DIR="$ROOT/vendor"
PIPER_VER="${PIPER_VER:-2023.11.14-2}"
WHISPER_VER="${WHISPER_VER:-v1.7.4}"

vendor_piper() {
    local dest="$VENDOR_DIR/piper"
    if [[ -x "$dest/piper" ]]; then
        ok "vendored piper already present ($dest/piper)."
        return 0
    fi
    log "Fetching piper $PIPER_VER from GitHub releases…"
    mkdir -p "$VENDOR_DIR"
    local url="https://github.com/rhasspy/piper/releases/download/${PIPER_VER}/piper_linux_x86_64.tar.gz"
    local tgz="$VENDOR_DIR/piper.tar.gz"
    curl -fL --progress-bar -o "$tgz" "$url"
    tar -xzf "$tgz" -C "$VENDOR_DIR"
    rm -f "$tgz"
    # The tarball expands to vendor/piper/ — confirm.
    if [[ ! -x "$dest/piper" ]]; then
        err "piper tarball layout changed; expected $dest/piper"
        exit 1
    fi
    ok "piper installed to $dest/"
}

vendor_whisper() {
    local dest="$VENDOR_DIR/whisper.cpp"
    if [[ -x "$dest/whisper-cli" ]]; then
        ok "vendored whisper-cli already present ($dest/whisper-cli)."
        return 0
    fi
    log "Fetching whisper.cpp $WHISPER_VER (build from source)…"
    mkdir -p "$VENDOR_DIR"
    local clone_dir="$VENDOR_DIR/whisper.cpp.src"
    if [[ ! -d "$clone_dir/.git" ]]; then
        git clone --depth 1 --branch "$WHISPER_VER" \
            https://github.com/ggerganov/whisper.cpp.git "$clone_dir"
    fi
    (
        cd "$clone_dir"
        # Prefer cmake (modern whisper.cpp builds), fall back to make.
        if command -v cmake >/dev/null 2>&1; then
            cmake -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
            cmake --build build -j --config Release
            install -Dm755 build/bin/whisper-cli "$dest/whisper-cli"
        else
            make -j whisper-cli
            install -Dm755 whisper-cli "$dest/whisper-cli"
        fi
    )
    ok "whisper-cli installed to $dest/whisper-cli"
}

if (( ${#VENDORED[@]} > 0 )); then
    log "Building/fetching vendored binaries: ${VENDORED[*]}"
    for v in "${VENDORED[@]}"; do
        case "$v" in
            piper-tts)   vendor_piper ;;
            whisper-cli) vendor_whisper ;;
            *) warn "unknown vendored entry: $v" ;;
        esac
    done
    # Hint the wrapper how to find them. The voice script honours
    # PIPER_BIN and WHISPER_BIN environment overrides (see backends/
    # common_piper/tts.py and common_whisper_cpp/stt.py).
    VOICE_ENV="$ROOT/.voice-env"
    {
        echo "# Auto-generated by install.sh — do not edit by hand."
        echo "export PIPER_BIN=\"$VENDOR_DIR/piper/piper\""
        echo "export WHISPER_BIN=\"$VENDOR_DIR/whisper.cpp/whisper-cli\""
    } > "$VOICE_ENV"
    ok "Wrote $VOICE_ENV with PIPER_BIN/WHISPER_BIN."
fi

# ---------- 3. Python venv + pip deps ---------------------------------------
log "Setting up Python venv…"
PYTHON_BIN="python3"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || PYTHON_BIN="python"

if [[ ! -x venv/bin/python ]]; then
    "$PYTHON_BIN" -m venv venv
    ok "venv created ($PYTHON_BIN)."
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
    case "$DEFAULT_VOICE" in
        es_AR-daniela-high)    ./download-voice.sh es/es_AR/daniela/high ;;
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

# ---------- 7. .desktop launcher + XDG autostart ----------------------------
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

# XDG autostart (everywhere except Hyprland, which has its own
# exec-once mechanism handled in step 8).
if (( ! IS_HYPRLAND )); then
    AUTOSTART_DIR="$HOME/.config/autostart"
    mkdir -p "$AUTOSTART_DIR"
    cat > "$AUTOSTART_DIR/voice-opencode.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Voice OpenCode Tray
Exec=$ROOT/voice tray
Icon=$ROOT/icons/voice-idle.svg
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=false
EOF
    ok "XDG autostart installed: $AUTOSTART_DIR/voice-opencode.desktop"
fi

# ---------- 8. Compositor binds ---------------------------------------------
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
elif (( IS_XFCE )) && command -v xfconf-query >/dev/null 2>&1; then
    log "Wiring XFCE keybindings via xfconf-query…"
    # XFCE doesn't support press/release semantics natively, so F9
    # becomes toggle (start if idle, stop if recording). The Python
    # tts.toggle action handles both phases — see ADR-0001 for the
    # rationale on falling back to toggle outside Hyprland.
    CHAN="xfce4-keyboard-shortcuts"
    BIND_KEY="/commands/custom/F9"
    BIND_RST="/commands/custom/<Super>F9"
    xfconf-query -c "$CHAN" -p "$BIND_KEY" --create -t string -s "$ROOT/voice toggle" 2>/dev/null \
        || xfconf-query -c "$CHAN" -p "$BIND_KEY" -s "$ROOT/voice toggle"
    xfconf-query -c "$CHAN" -p "$BIND_RST" --create -t string -s "$ROOT/voice reset" 2>/dev/null \
        || xfconf-query -c "$CHAN" -p "$BIND_RST" -s "$ROOT/voice reset"
    ok "XFCE keybinds set: F9 → voice toggle ; Super+F9 → voice reset"
    warn "XFCE has no press/release events — F9 is bound to toggle (start/stop)."
else
    warn "No supported keybind backend (not Hyprland, not XFCE)."
    warn "Bind F9 manually in your DE:"
    warn "  toggle  → $ROOT/voice toggle   (or press → start, release → stop)"
    warn "  reset   → $ROOT/voice reset"
fi

# ---------- 9. opencode MCP integration -------------------------------------
log "Wiring opencode MCP integration…"
OC_CFG_DIR="$HOME/.config/opencode"
OC_CFG="$OC_CFG_DIR/opencode.json"
mkdir -p "$OC_CFG_DIR"

# The permission block below is REQUIRED for headless ``opencode serve``
# mode. Without it, any MCP tool that writes outside the workspace
# (typically capture_screen → \$XDG_RUNTIME_DIR/voice-opencode/) fires a
# ``permission.asked`` bus event that has no TUI to answer, hanging every
# voice turn until the 600 s HTTP timeout. See _ai/CHANGELOG.md
# 2026-05-19.
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
  },
  "permission": {
    "read": "allow",
    "edit": "allow",
    "glob": "allow",
    "grep": "allow",
    "list": "allow",
    "bash": "allow",
    "task": "allow",
    "external_directory": "allow",
    "todowrite": "allow",
    "question": "allow",
    "webfetch": "allow",
    "websearch": "allow",
    "repo_clone": "allow",
    "repo_overview": "allow",
    "lsp": "allow",
    "doom_loop": "allow",
    "skill": "allow"
  }
}
EOF
    ok "Created $OC_CFG with voice_desktop MCP + headless permission allow-list."
else
    if ! grep -q '"voice_desktop"' "$OC_CFG"; then
        warn "$OC_CFG exists but doesn't reference voice_desktop."
        warn "Add this to its 'mcp' object manually:"
        warn '    "voice_desktop": {"type":"local","command":["'"$ROOT"'/voice","mcp","serve"],"enabled":true}'
    else
        ok "voice_desktop already present in $OC_CFG"
    fi
    if ! grep -q '"external_directory"' "$OC_CFG"; then
        warn "$OC_CFG is missing the 'permission.external_directory' rule."
        warn "Without it, headless 'opencode serve' will hang on capture_screen."
        warn "Add a top-level block manually:"
        warn '    "permission": { "external_directory": "allow", "bash": "allow", ... }'
        warn "See _ai/STATE.md → opencode integration for the full block."
    else
        ok "permission allow-list present in $OC_CFG"
    fi
fi

# ---------- done -------------------------------------------------------------
echo
log "Done. Quick check:"
./voice state || warn "Server may need a moment to boot. Try: systemctl --user status opencode-serve"
echo
if (( IS_HYPRLAND )); then
    ok "Reload Hyprland (super+shift+r or 'hyprctl reload') to pick up new binds."
elif (( IS_XFCE )); then
    ok "XFCE keybinds applied. Log out and back in if F9 doesn't fire immediately."
fi
ok "Launch the tray now with:  ./voice tray  &"
(( IS_HYPRLAND || IS_XFCE )) || ok "(Bind F9 manually in your DE — see warnings above.)"
