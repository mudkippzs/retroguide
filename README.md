# RetroGuide

A local-AI powered **retro TV guide** for your own media library. Point it at
your TV and movie drives and it programs a full week of channels the way
television *used to feel* — Saturday-morning cartoons, after-school toons,
primetime drama, an evening film, and late-night adult animation — then plays it
all in an embedded **mpv** player. The "playlist" *is* the schedule: jump to any
day/time, or hit **LIVE** and tune in mid-broadcast like the old days.

Everything runs on your machine. A local **Ollama** model writes spoiler-free,
90s-TV-Guide-style blurbs; free **TVmaze** metadata (no key, no billing) fills in
accurate plots, cast, genres and episode titles. No subscriptions, no ads, no
reruns, no licensing — and it grows on its own as you add to your collection.

> Why it's good: it merges on-demand viewing with scheduled programming, but
> drops the bad parts of both — no ads, no waiting for next season, no
> subscriptions, no "not available in your region". You don't manage a watch
> list; new files just get woven into the schedule over time.

![RetroGuide](docs/screenshot.png)

## Features

- **Indexer** — walks your drives, untangles messy scene-release names with
  `guessit`, reads real runtimes/codecs with `ffprobe`, caches to SQLite.
- **Free enrichment** — **TVmaze** metadata for TV (no key/billing), optional
  TMDB for films if you have a key, plus a local LLM that rewrites synopses into
  punchy, spoiler-free retro listings. Works fully offline if nothing's reachable.
- **Themed channels** — cartoon, drama, action, comedy, cinema, anime, adult
  animation, science, stand-up and +1 timeshifts, each with day-part templates.
  TV shows advance **episode by episode** from S01E01 via a persistent
  per-series playhead (abs_order cursor). Each series has an exclusive home
  channel, with title/episode cooldowns and a per-day airing cap so the grid
  stays regular but fresh. Partial/missing/unprobed files are excluded from
  the grid entirely.
- **Special events** — seasonal & spontaneous stunts take over The Movie Channel
  (Christmas cinema, a May-4th Star Wars bonanza, a Marvel origins weekend…),
  driven by automatic franchise/holiday tagging. Toggle in config.
- **Embedded player** — libmpv with full transport, autoplay to the next
  program, a **LIVE** button that seeks into whatever should be airing now,
  fullscreen/aspect controls, **audio-track selection** (defaults to English),
  **subtitles** toggle, and per-era **CRT** + **black-&-white** picture effects.
- **Two guide views** — the classic vertical "TV Guide magazine" listing
  (`time — Title: subtitle / blurb`) and a horizontal grid EPG, both live and
  both auto-following the current time.
- **Binge mode** — flip from **TV** to **Binge** and browse the whole catalog
  (series → seasons → episodes, or films) as a searchable tree. Build a queue
  by adding episodes, whole seasons, entire series or films; shuffle it, save
  it, load it back, or roll a **theme-random** queue from a genre/bucket.
  Playback supports **random** and **repeat** toggles.
- **Scheduling controls** — right-click any series in the Binge library to
  **start a show from a later season** (e.g. skip to S03) or **pin/block it on
  channels** (only on Comedy Central, never on ToonWorld…). Rules persist
  across rebuilds; clear them from the same menu.
- **LAN + remote streaming** — broadcast your channels to any browser on the
  network (or anywhere, over Tailscale/VPN). HLS for Apple devices, low-latency
  MP4 for everyone else. See [Watch from another device](#watch-from-another-device).
- **ROAMER: the phone app** — a mobile-first PWA at `/m` with LIVE, GUIDE and
  BINGE tabs. Full binge parity with the desktop (queue building, shuffle,
  saved lists, theme-random, repeat/random), native iOS playback (seek,
  pause/resume, fullscreen, AirPlay, real CC via WebVTT), cross-device resume,
  and ~30 min of live pause/rewind. Add it to your home screen and it behaves
  like an app. See [Watch from your phone](#watch-from-your-phone).
- **Retro skins** — 70s / 80s / 90s / 00s eras with CRT scanlines and an
  orthogonal B&W toggle, on both desktop and the web view.

## Requirements

- Linux with an X11 or XWayland display (for the desktop app)
- Python 3.12+ (tested on 3.14)
- `ffmpeg`/`ffprobe` and `mpv`/`libmpv`
- [Ollama](https://ollama.com) running locally with a chat model (defaults to
  `mistral-small3.2`) and `nomic-embed-text` — optional but recommended

## Install (one command)

```bash
git clone https://github.com/mudkippzs/retroguide.git
cd retroguide
./install.sh
```

`install.sh` detects your package manager (dnf / apt / pacman / zypper / brew),
installs `ffmpeg` + `mpv`, creates a virtualenv, installs the Python
requirements, and seeds `config.toml`. It's safe to re-run.

<details>
<summary>Manual install</summary>

```bash
sudo dnf install ffmpeg mpv mpv-libs     # or apt/pacman/zypper equivalents
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
cp config.example.toml config.toml
```
</details>

## Point it at your media

Edit **`config.toml`** and set your library roots — one or more folders each:

```toml
[library]
tv_roots    = ["/mnt/tv", "/mnt/tv2"]     # folders of TV shows
movie_roots = ["/mnt/movies"]             # folders of films
```

These can be local paths or mounted network shares (NFS/CIFS). RetroGuide reads
your files; it never moves or modifies them. Optional tuning lives right below
in the same section (`video_extensions`, `exclude_patterns`, `min_file_mb`).

(Optional) for retro blurbs and the best classification, have Ollama running:

```bash
ollama pull mistral-small3.2
ollama pull nomic-embed-text
```

## Run

```bash
./run.sh
```

On first launch hit **Build my TV** to run the whole pipeline
(scan → probe → enrich → schedule). After that, use the top-bar buttons:

- **↻ Rebuild** — reprogram the week (continues each series where it left off)
- **✨ Blurbs** — have the local model write retro teasers for the week
- **Refresh Library** — rescan drives for new files
- **⚙ Settings** — library paths, channel names/logos, era/CRT/B&W, streaming
- **Stream** — start the LAN broadcast server

## Watch from another device

Hit **Stream** in the app (or run `./.venv/bin/python -m tvguide.cli serve`) and
open the printed URL in any browser on your LAN. You join **whatever is airing
right now**, just like real TV, with an in-page guide and channel switcher.

- **Apple devices** (iPhone/iPad/Mac Safari) get native **HLS**.
- **Everything else** (Chrome/Firefox/Android) gets a lower-latency MP4 stream.
- **From anywhere:** put [Tailscale](https://tailscale.com) on the host and your
  device, then open `http://<host-tailscale-ip>:8722/` — your whole library,
  scheduled like TV, in an airport lounge on the other side of the world.

Streaming is capped (`[stream] max_streams`, default 4 concurrent transcodes) so
a flaky client can't overrun the host.

## Watch from your phone

**ROAMER** is the mobile surface: open `/m` (or just `/` — phones are routed
there automatically) on the stream server. Three tabs:

- **LIVE** — tune any channel, join mid-broadcast like real TV. On iPhone the
  native scrubber pauses/rewinds live TV within `[stream] hls_list_size`
  (450 × 4s segments ≈ **30 minutes** in the shipped config).
- **GUIDE** — the magazine listing for the channel you're on, auto-scrolled to
  what's on now.
- **BINGE** — your whole catalog: series → seasons → episodes and films, with
  search, watched ticks and resume markers. Tap anything to play, or build a
  queue (add episodes/seasons/series/films, reorder, shuffle, save/load named
  lists, 🎲 theme-random) with **repeat** and **random** toggles and
  auto-advance. The queue is *shared with the desktop app* — build it on
  either, play it on both.

Playback is the **native iOS player** over HLS, so you get the real seek bar,
hours-long pause/resume, fullscreen, AirPlay and Picture-in-Picture. Subtitles
are a genuine iOS CC menu when the file has text subs (WebVTT sidecar), or
one-tap burn-in for bitmap subs (PGS/DVD). **Aspect ratio** cycles fit → fill →
stretch inline; **volume** is the iPhone's hardware buttons / native player
(iOS doesn't allow web volume sliders). Progress checkpoints to the server, so
closing Safari mid-episode and reopening — on any device — resumes where you
left off. On-demand sessions transcode ahead of you; the "TRANSCODING AHEAD"
pill shows how much is ready.

**From anywhere (Tailscale):** with Tailscale on the host and your phone,

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8722
```

then open `https://<host>.<tailnet>.ts.net:8443/m` on the phone and use
Safari's **Add to Home Screen** — it launches fullscreen like a native app.
(The example uses port 8443 because 443 is often taken; any free port works.
The serve config persists in tailscaled across reboots.)

### Always-on hosting

`deploy/install.sh` installs two systemd **user** services (and enables
linger, so they start at boot and survive logout):

- `retroguide-stream.service` — the stream server, `Restart=always`.
  Check: `systemctl --user status retroguide-stream` and
  `curl -s localhost:8722/api/health`.
- `retroguide-schedule.timer` — nightly 03:30 `scan → probe → schedule` so the
  7-day grid never runs dry while you're away. Check:
  `systemctl --user list-timers retroguide-schedule.timer`.

Notes for unattended weeks: keep the host awake (GNOME: *Power → automatic
suspend off*), keep `tailscaled` enabled at boot (it is by default), and if
the desktop app is running its own **Stream** server it already owns port 8722
— toggle it off (or close the app) so the service can bind; the app's button
will simply report the port as busy otherwise.

### Command line

The same pipeline is scriptable:

```bash
./.venv/bin/python -m tvguide.cli scan      # build the catalog
./.venv/bin/python -m tvguide.cli probe     # read runtimes (slow)
./.venv/bin/python -m tvguide.cli enrich    # TVmaze/TMDB + buckets + tags
./.venv/bin/python -m tvguide.cli schedule  # program the week
./.venv/bin/python -m tvguide.cli blurbs    # LLM retro teasers
./.venv/bin/python -m tvguide.cli serve     # LAN/remote stream server
./.venv/bin/python -m tvguide.cli stats
./.venv/bin/python -m tvguide.cli doctor   # integrity check
```

## Configuration notes

- **Broadcast day** starts at `schedule.day_start_hour` (default 6am), so
  late-night content belongs to the previous day's listings.
- **Channels** can be renamed and given logo bugs under `[channels]` /
  `[channel_logos]`; their day-parts are defined in `tvguide/schedule/dayparts.py`.
- **Mis-classified shows** can be re-bucketed under `[bucket_overrides]`
  (`"3rd Rock from the Sun" = "sitcom"`), and cryptic release folders can be
  mapped to their real title under `[title_aliases]`
  (`"SUPERMAN T A S" = "Superman: The Animated Series"`). Both survive
  reindexes.
- The catalog/schedule live in a SQLite DB under your platform data dir
  (e.g. `~/.local/share/RetroGuide/retroguide.db`); logs sit alongside it.

## How it fits together

```
drives ──▶ scan (guessit) ──▶ probe (ffprobe) ──▶ catalog (SQLite)
                                                      │
                          TVmaze/TMDB + Ollama ──▶ enrich (overviews, buckets,
                                                      │       tags, blurbs)
                                          scheduler ──▶ programs (7-day grid)
                                                      │
                            PySide6 UI + libmpv ──▶ watch  ──┐
                                                      │       ├──▶ LAN / Tailscale
                                       stream server ──▶ HLS + MP4 to any browser
```

## License

RetroGuide is released under the **[PolyForm Noncommercial License 1.0.0](LICENSE)**.
You're welcome to use, fork, and modify it for any **noncommercial** purpose
(personal use, hobby projects, learning, charities/schools). Commercial use
requires a separate license from the author. Copyright © 2026 mudkippzs.
