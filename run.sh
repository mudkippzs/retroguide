#!/usr/bin/env bash
# Launch RetroGuide from its virtualenv.
set -e
cd "$(dirname "$0")"

# These leak from the Cursor AppImage and confuse Python's executable
# resolution; strip them so the venv interpreter is used correctly.
unset APPIMAGE APPDIR ARGV0

# mpv embeds most reliably into an X11/XWayland surface.
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"

exec ./.venv/bin/python3 -m tvguide "$@"
