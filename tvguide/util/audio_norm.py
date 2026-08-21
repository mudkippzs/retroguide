"""Shared loudness normalization for desktop (mpv) and stream (ffmpeg).

``dynaudnorm`` is single-pass and seek-friendly — a better fit for live TV
tune-in than two-pass loudnorm. Levels settle within a second or two of
playback so channel changes don't blast or whisper.
"""

# ffmpeg / lavfi filtergraph fragment (no surrounding brackets).
AUDIO_NORM = "dynaudnorm=f=150:g=15:p=0.95"
