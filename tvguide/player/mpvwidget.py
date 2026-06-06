"""An embedded libmpv video surface with Qt signals for transport state.

Rendering uses libmpv's *render API* drawing into a ``QOpenGLWidget``'s
framebuffer rather than handing mpv a window id. Window-id embedding only
works on X11; the render API embeds correctly on Wayland too (Qt owns the GL
surface and mpv just draws into our FBO).
"""
from __future__ import annotations

import locale

import mpv
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget


def _get_proc_address(_ctx, name):
    """Resolve an OpenGL function pointer via Qt's current GL context."""
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    if isinstance(name, bytes):
        name = name.decode("utf-8")
    addr = glctx.getProcAddress(name)
    return int(addr) if addr else 0


class MpvWidget(QOpenGLWidget):
    positionChanged = Signal(float)     # current time (s)
    durationChanged = Signal(float)     # total duration (s)
    pausedChanged = Signal(bool)
    endReached = Signal()
    fileLoaded = Signal()
    loadFailed = Signal(str)            # human-readable reason
    _frameReady = Signal()              # emitted from mpv's render thread

    _ASPECT_MODES = ("AUTO", "FILL", "STRETCH", "4:3", "16:9")

    def __init__(self, parent: QOpenGLWidget | None = None):
        super().__init__(parent)
        locale.setlocale(locale.LC_NUMERIC, "C")

        self._mpv = mpv.MPV(
            vo="libmpv",              # required for the render API
            hwdec="auto-safe",
            osc=False,
            osd_level=0,
            input_default_bindings=False,
            input_vo_keyboard=False,
            keep_open="yes",          # don't unload at EOF; we advance manually
            ytdl=False,
            # Default to an English audio track (many rips default to a foreign
            # dub) and start with subtitles off.
            alang="eng,en,english",
            slang="eng,en,english",
            sid="no",
            log_handler=self._on_log,
        )
        self._ctx: mpv.MpvRenderContext | None = None
        # Keep a strong reference so the ctypes callback isn't garbage-collected.
        self._proc_addr = mpv.MpvGlGetProcAddressFn(_get_proc_address)
        self._duration: float = 0.0
        self._aspect_idx = 0
        self._pending: tuple[str, float] | None = None

        # mpv signals its "needs redraw" callback on a worker thread; bounce it
        # to the GUI thread so update() (and thus paintGL) runs safely there.
        self._frameReady.connect(self.update, Qt.ConnectionType.QueuedConnection)
        self._wire_observers()

    # -- GL lifecycle -------------------------------------------------------
    def initializeGL(self) -> None:
        self._ctx = mpv.MpvRenderContext(
            self._mpv, "opengl",
            opengl_init_params={"get_proc_address": self._proc_addr},
        )
        self._ctx.update_cb = self._frameReady.emit
        if self._pending is not None:
            path, start = self._pending
            self._pending = None
            self.play(path, start)

    def paintGL(self) -> None:
        if self._ctx is None:
            return
        ratio = self.devicePixelRatioF()
        w = max(1, int(self.width() * ratio))
        h = max(1, int(self.height() * ratio))
        self._ctx.render(
            flip_y=True,
            opengl_fbo={"w": w, "h": h, "fbo": self.defaultFramebufferObject()},
        )

    def _on_log(self, level: str, prefix: str, text: str) -> None:
        # Surface decode failures (e.g. missing HEVC codec) instead of a silent
        # black screen. Runs on mpv's thread; the queued signal hops to the GUI.
        msg = text.strip()
        low = msg.lower()
        if "failed to initialize a decoder" in low or "could not open codec" in low:
            codec = ""
            if "'" in msg:
                codec = msg.split("'")[1].upper()
            reason = f"No {codec} decoder installed" if codec else "Unsupported codec"
            self.loadFailed.emit(reason)

    # -- observers ----------------------------------------------------------
    def _wire_observers(self) -> None:
        @self._mpv.property_observer("time-pos")
        def _on_time(_name, value):  # noqa: ANN001
            if value is not None:
                self.positionChanged.emit(float(value))

        @self._mpv.property_observer("duration")
        def _on_dur(_name, value):  # noqa: ANN001
            if value:
                self._duration = float(value)
                self.durationChanged.emit(self._duration)

        @self._mpv.property_observer("pause")
        def _on_pause(_name, value):  # noqa: ANN001
            self.pausedChanged.emit(bool(value))

        @self._mpv.property_observer("eof-reached")
        def _on_eof(_name, value):  # noqa: ANN001
            if value:
                self.endReached.emit()

        @self._mpv.event_callback("file-loaded")
        def _on_loaded(_event):  # noqa: ANN001
            self.fileLoaded.emit()

    # -- transport ----------------------------------------------------------
    def play(self, path: str, start_sec: float = 0.0) -> None:
        # If GL isn't ready yet, remember the request and start it in initializeGL.
        if self._ctx is None:
            self._pending = (path, start_sec)
            return
        try:
            if start_sec > 1:
                # Open directly at the offset so mpv does one fast seek on load
                # instead of decoding from 0 -- crucial for "tune in live".
                self._mpv.loadfile(path, "replace", start=str(int(start_sec)))
            else:
                self._mpv.loadfile(path, "replace")
            self._mpv.pause = False
        except Exception:
            try:
                self._mpv.play(path)
            except Exception:
                pass

    def toggle_pause(self) -> None:
        self._mpv.pause = not self._mpv.pause

    def set_paused(self, paused: bool) -> None:
        self._mpv.pause = paused

    def is_paused(self) -> bool:
        try:
            return bool(self._mpv.pause)
        except Exception:
            return True

    def seek_absolute(self, seconds: float) -> None:
        try:
            self._mpv.seek(seconds, reference="absolute", precision="keyframes")
        except Exception:
            pass

    def seek_relative(self, delta: float) -> None:
        try:
            self._mpv.seek(delta, reference="relative")
        except Exception:
            pass

    def set_volume(self, vol: int) -> None:
        try:
            self._mpv.volume = max(0, min(130, int(vol)))
        except Exception:
            pass

    def set_mute(self, mute: bool) -> None:
        try:
            self._mpv.mute = mute
        except Exception:
            pass

    def cycle_aspect(self) -> str:
        """Cycle AUTO -> FILL -> STRETCH -> 4:3 -> 16:9 and return the label."""
        self._aspect_idx = (self._aspect_idx + 1) % len(self._ASPECT_MODES)
        mode = self._ASPECT_MODES[self._aspect_idx]
        try:
            self._mpv["keepaspect"] = True
            self._mpv["panscan"] = 0.0
            self._mpv["video-aspect-override"] = "-1"
            if mode == "FILL":
                self._mpv["panscan"] = 1.0
            elif mode == "STRETCH":
                self._mpv["keepaspect"] = False
            elif mode == "4:3":
                self._mpv["video-aspect-override"] = "4:3"
            elif mode == "16:9":
                self._mpv["video-aspect-override"] = "16:9"
        except Exception:
            pass
        return mode

    # -- audio / subtitle tracks -------------------------------------------
    def _tracks(self, kind: str) -> list[dict]:
        try:
            tl = self._mpv.track_list or []
        except Exception:
            return []
        out = []
        for t in tl:
            if t.get("type") != kind:
                continue
            lang = t.get("lang") or ""
            title = t.get("title") or ""
            label = " ".join(p for p in (lang.upper() if lang else "", title) if p)
            out.append({
                "id": t.get("id"),
                "label": label or f"Track {t.get('id')}",
                "selected": bool(t.get("selected")),
            })
        return out

    def audio_tracks(self) -> list[dict]:
        return self._tracks("audio")

    def set_audio(self, track_id: int) -> None:
        try:
            self._mpv.aid = track_id
        except Exception:
            pass

    def subtitle_tracks(self) -> list[dict]:
        return self._tracks("sub")

    def subtitles_on(self) -> bool:
        try:
            return self._mpv.sid not in (None, False, "no")
        except Exception:
            return False

    def toggle_subtitles(self) -> bool:
        """Flip subtitles on/off; returns the new state."""
        try:
            if self.subtitles_on():
                self._mpv.sid = "no"
                return False
            subs = self.subtitle_tracks()
            self._mpv.sid = subs[0]["id"] if subs else "auto"
            return bool(subs)
        except Exception:
            return False

    def set_subtitle(self, track_id) -> None:
        try:
            self._mpv.sid = track_id
        except Exception:
            pass

    def set_grayscale(self, on: bool) -> None:
        """Desaturate the picture to simulate a black-and-white set."""
        try:
            self._mpv["vf"] = "hue=s=0" if on else ""
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self._mpv.command("stop")
        except Exception:
            pass

    @property
    def duration(self) -> float:
        return self._duration

    def shutdown(self) -> None:
        try:
            if self._ctx is not None:
                self.makeCurrent()
                self._ctx.free()
                self._ctx = None
                self.doneCurrent()
        except Exception:
            pass
        try:
            self._mpv.terminate()
        except Exception:
            pass
