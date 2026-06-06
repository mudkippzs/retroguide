#!/usr/bin/env bash
#
# RetroGuide one-shot installer.
#
#   ./install.sh
#
# Installs the system dependencies (ffmpeg + mpv/libmpv), creates a Python
# virtualenv, installs the Python requirements, and seeds your config.toml.
# Safe to re-run; it only does what's missing.
#
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!! \033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx \033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Python (need 3.12+)
# ---------------------------------------------------------------------------
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || die "python3 not found. Install Python 3.12+ and re-run."
"$PY" - <<'EOF' || die "Python 3.12+ required."
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
EOF
say "Using $("$PY" --version)"

# ---------------------------------------------------------------------------
# 2. System dependencies: ffmpeg, ffprobe, mpv/libmpv
# ---------------------------------------------------------------------------
detect_pm() {
  for pm in dnf apt-get pacman zypper brew; do
    command -v "$pm" >/dev/null 2>&1 && { echo "$pm"; return; }
  done
}

install_system_deps() {
  local pm; pm="$(detect_pm)"
  local need_ff=1 need_mpv=1
  command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 && need_ff=0
  # libmpv: mpv CLI is a good proxy; python-mpv loads the shared lib.
  command -v mpv >/dev/null 2>&1 && need_mpv=0

  if [ "$need_ff" -eq 0 ] && [ "$need_mpv" -eq 0 ]; then
    say "System deps already present (ffmpeg, mpv)."
    return
  fi

  if [ -z "$pm" ]; then
    warn "No known package manager found. Please install manually:"
    warn "  - ffmpeg + ffprobe"
    warn "  - mpv / libmpv"
    return
  fi

  say "Installing system deps via $pm (may prompt for sudo)..."
  case "$pm" in
    dnf)     sudo dnf install -y ffmpeg mpv mpv-libs ;;
    apt-get) sudo apt-get update && sudo apt-get install -y ffmpeg mpv libmpv2 || \
             sudo apt-get install -y ffmpeg mpv libmpv1 ;;
    pacman)  sudo pacman -Sy --needed --noconfirm ffmpeg mpv ;;
    zypper)  sudo zypper install -y ffmpeg mpv libmpv2 ;;
    brew)    brew install ffmpeg mpv ;;
  esac
}
install_system_deps

# ---------------------------------------------------------------------------
# 3. Virtualenv + Python requirements
# ---------------------------------------------------------------------------
if [ ! -d .venv ]; then
  say "Creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi
say "Installing Python requirements"
./.venv/bin/python -m pip install --upgrade pip >/dev/null
./.venv/bin/python -m pip install -r requirements.txt

# ---------------------------------------------------------------------------
# 4. Seed config
# ---------------------------------------------------------------------------
if [ ! -f config.toml ]; then
  cp config.example.toml config.toml
  say "Created config.toml from the example."
  CONFIG_FRESH=1
else
  say "config.toml already exists - leaving it untouched."
  CONFIG_FRESH=0
fi

# ---------------------------------------------------------------------------
# 5. Friendly next steps
# ---------------------------------------------------------------------------
cat <<EOF

$(printf '\033[1;32m✓ RetroGuide is installed.\033[0m')

Next steps:

  1. Point it at your media. Edit config.toml:

       [library]
       tv_roots    = ["/mnt/tv"]        # one or more folders of TV shows
       movie_roots = ["/mnt/movies"]    # one or more folders of films

  2. (Recommended) Have Ollama running for retro blurbs + smart metadata:

       ollama pull mistral-small3.2
       ollama pull nomic-embed-text

  3. Launch the app:

       ./run.sh

     On first run, hit "Build my TV" to scan -> probe -> enrich -> schedule.

EOF
if [ "${CONFIG_FRESH:-0}" -eq 1 ]; then
  warn "Don't forget step 1 - config.toml still points at the example paths."
fi
