"""Sound-effect and speech playback for the cauldron.

Run directly to check audio output:

    .venv/bin/python audio.py                              # Play the bubbling effect once.
    .venv/bin/python audio.py --loop 5                     # Loop the bubbling effect for 5s.
    .venv/bin/python audio.py --ambience 10                # Bubbling + laugh, as the live show runs it.
    .venv/bin/python audio.py --ambience 10 --laugh-volume 0.3  # ...at a different relative balance.
    .venv/bin/python audio.py generated/violet.mp3         # Play a generated witch line.
    .venv/bin/python audio.py generated/violet.mp3 --voice-volume 0.6  # ...at a different volume.
"""

from __future__ import annotations

import argparse
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pygame

from witches import WITCHES, Witch

ALSA_DEVICE = "plughw:Headphones,0"
"""The 3.5mm jack. ALSA's bare "default" device is unreliable on this Pi
-- with the HDMI outputs and the USB webcam's audio both also present as
sound cards, it can resolve to one of those instead, which fails to
open."""

ASSETS_DIR = Path("assets")
GENERATED_DIR = Path("generated")
"""Where voice_generator.py writes <witch>.mp3."""
BUBBLE_SFX_PATH = ASSETS_DIR / "bubbling.wav"
"""Bubbling sound effect from Pixabay:
https://pixabay.com/sound-effects/household-cauldron-boiling-173607/"""
LAUGH_SFX_PATH = ASSETS_DIR / "witch-laugh.wav"
"""Witch laughing sound effect from Pixabay:
https://pixabay.com/sound-effects/people-witch-laugh-28961/"""
_SPLASH_SOURCE_PATH = ASSETS_DIR / "splash.mp3"
"""Water splash sound effect from Pixabay:
https://pixabay.com/sound-effects/film-special-effects-water-splash-229315/"""
SPLASH_SFX_PATH = GENERATED_DIR / "_splash.wav"
"""Volume-scaled .wav copy of _SPLASH_SOURCE_PATH, rendered once by
setup() (see _ensure_splash_audio()) -- aplay (used for .wav playback,
see play_file()) has no live volume flag the way mpg123's -f does, so
matching SPLASH_VOLUME means baking it into the file itself. Playing a
.wav through aplay instead of the source .mp3 through mpg123 also cuts
per-call startup latency a lot on this hardware (benchmarked: ~0.12s vs
~0.4s for a clip this short) -- worth it for a cue meant to land the
instant something drops in, unlike a multi-second voice line where the
difference wouldn't register."""

BUBBLE_VOLUME: float = 1.0
"""Relative volume (0.0-1.0) for the bubbling ambience loop, paired with
LAUGH_VOLUME below. pygame can only scale a Sound down, never up, so the
laugh -- measured at roughly 7x bubbling's RMS level in the source files
-- is turned way down rather than trying to boost bubbling past its
natural volume. Tune with --bubble-volume/--laugh-volume on the
--ambience test below rather than guessing blind."""
LAUGH_VOLUME: float = 0.3
"""Relative volume (0.0-1.0) for the laugh ambience loop -- see
BUBBLE_VOLUME above."""
IDLE_BUBBLE_VOLUME: float = 0.55
"""Medium bubbling level -- for cauldron_controller's idle ambience (just
a background fireplace-like presence while the rainbow chases, not the
"something's brewing" cue used while actually waiting on an API call, so
no laugh plays alongside it either -- see start_bubbling()'s ``laugh``)."""
SPLASH_VOLUME: float = 0.6
"""trivia_game.py's (and others') answer/drop-splash cue. Baked into
SPLASH_SFX_PATH by _ensure_splash_audio(), not applied at play time --
unlike VOICE_VOLUME/WITCH_VOLUMES, which are live mpg123 -f scales."""

VOICE_VOLUME: float = 0.3
"""Default witch-line (TTS) playback volume, used by the CLI test below
when no specific witch is given. The live show instead uses
WITCH_VOLUMES, so each witch can be balanced independently -- some
voices render quieter than others at the same ElevenLabs settings.
Applied via mpg123's -f scale (32768 = full volume); see play_file()."""

WITCH_VOLUMES: dict[Witch, float] = {w: p.volume for w, p in WITCHES.items()}

_bubble_sfx: pygame.mixer.Sound | None = None
_laugh_sfx: pygame.mixer.Sound | None = None


def _ensure_splash_audio() -> None:
    """Pre-render SPLASH_SFX_PATH from _SPLASH_SOURCE_PATH at SPLASH_VOLUME,
    if not already cached. One-time, near-instant -- same technique as
    potion_play.py's _ensure_bubble_audio()."""
    if SPLASH_SFX_PATH.exists():
        return
    SPLASH_SFX_PATH.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(_SPLASH_SOURCE_PATH),
            "-filter:a", f"volume={SPLASH_VOLUME}",
            str(SPLASH_SFX_PATH),
        ],
        check=True,
    )


def setup() -> None:
    """Initialize the pygame mixer and pre-render the splash SFX (see
    _ensure_splash_audio()). Call once at startup."""
    pygame.mixer.init()
    _ensure_splash_audio()


def _bubble() -> pygame.mixer.Sound:
    """Return the shared bubbling Sound, loading it on first use."""
    global _bubble_sfx
    if _bubble_sfx is None:
        _bubble_sfx = pygame.mixer.Sound(str(BUBBLE_SFX_PATH))
        _bubble_sfx.set_volume(BUBBLE_VOLUME)
    return _bubble_sfx


def _laugh() -> pygame.mixer.Sound:
    """Return the shared witch-laugh Sound, loading it on first use."""
    global _laugh_sfx
    if _laugh_sfx is None:
        _laugh_sfx = pygame.mixer.Sound(str(LAUGH_SFX_PATH))
        _laugh_sfx.set_volume(LAUGH_VOLUME)
    return _laugh_sfx


def start_bubbling(laugh: bool = True, volume: float | None = None) -> None:
    """Start the bubbling cauldron loop, optionally with a witch's laugh
    looping over it.

    No-op if bubbling is already playing -- note that means a call with a
    different ``laugh``/``volume`` while it's already running has no
    effect; stop_bubbling() first if the caller needs to change either. The
    laugh runs on its own mixer channel so it overlaps the bubbling loop
    rather than interrupting it. ``volume`` defaults to BUBBLE_VOLUME, but
    is always applied explicitly (not just at the Sound's first load) so a
    caller that previously set a custom volume doesn't leak into the next.
    """
    sfx = _bubble()
    if sfx.get_num_channels() == 0:
        sfx.set_volume(BUBBLE_VOLUME if volume is None else volume)
        sfx.play(loops=-1)
        if laugh:
            _laugh().play(loops=-1)


def stop_bubbling() -> None:
    """Stop the bubbling loop and any still-playing laugh.

    Safe to call even if neither ever started, or if the mixer is currently
    quit -- play_file() releases it for the duration of each TTS line (see
    its docstring), and a mode-switch button press can land in that window.
    There is nothing playing to stop in that case anyway: quitting the mixer
    already silences everything.
    """
    if pygame.mixer.get_init() is None:
        return
    _bubble().stop()
    _laugh().stop()


def start_laugh() -> None:
    """Loop the witch-laugh effect on its own, layered over whatever else
    is already playing.

    start_bubbling(laugh=True) plays bubbling and the laugh together as one
    pygame-mixer-based pair -- no good for trivia_game.py, whose own
    ambience loop runs as an independent aplay subprocess (see
    start_bubbling_background()) specifically so voice lines can play
    right over it. This is just the laugh half, for a caller that wants
    react mode's "something's happening" cue (bubbling+laugh+flicker, see
    cauldron_controller._start_ambience()) without also restarting
    bubbling. No-op if the mixer is currently quit (play_file() releases
    it around every voice line) or the laugh is already looping.
    """
    if pygame.mixer.get_init() is None:
        return
    sfx = _laugh()
    if sfx.get_num_channels() == 0:
        sfx.play(loops=-1)


def stop_laugh() -> None:
    """Stop the laugh loop. Safe to call any time."""
    if pygame.mixer.get_init() is None:
        return
    _laugh().stop()


def set_ambience_intensity(level: float) -> None:
    """Scale the already-playing bubbling/laugh loop's volume by ``level`` (0-1).

    For brew_game.py: rather than swapping between separate pre-rendered
    "calm"/"chaotic" sound files (no live pitch/speed-shifting exists here --
    see play_file()'s docstring), the one existing ambience loop just gets
    louder as the brew gets more out of control. pygame.mixer.Sound.set_volume()
    is safe to call repeatedly on an already-playing Sound. No-op if bubbling
    was never started (nothing to scale) or the mixer is currently quit --
    same situations stop_bubbling() already guards against.
    """
    if pygame.mixer.get_init() is None:
        return
    level = max(0.0, min(1.0, level))
    _bubble().set_volume(BUBBLE_VOLUME * (0.3 + 0.7 * level))
    _laugh().set_volume(LAUGH_VOLUME * (0.2 + 0.8 * level))


def _bg_bubble_path(volume: float) -> Path:
    return GENERATED_DIR / f"_bubbling_bg_{volume:.2f}.wav"


def _ensure_bg_bubble_audio(volume: float) -> Path:
    """Pre-render one volume-scaled copy of the bubbling SFX via ffmpeg, if
    not already cached -- aplay (used by start_bubbling_background() below)
    has no live volume control, unlike mpg123's -f flag used elsewhere (see
    play_file()), so matching a given volume means pre-scaling the file
    itself instead. Same technique as potion_play.py's
    _ensure_bubble_audio(), just one fixed level rather than several."""
    path = _bg_bubble_path(volume)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(BUBBLE_SFX_PATH),
                "-filter:a",
                f"volume={volume}",
                str(path),
            ],
            check=True,
        )
    return path


_bg_bubble_thread: threading.Thread | None = None
_bg_bubble_stop: threading.Event | None = None


def start_bubbling_background(volume: float = IDLE_BUBBLE_VOLUME) -> None:
    """Loop the bubbling SFX as its own aplay subprocess, independent of
    pygame.mixer entirely -- unlike start_bubbling() above, play_file()
    never needs to stop this before a voice line plays, since two plain
    subprocesses can share the ALSA device directly without the glitching
    pygame's mixer causes against a subprocess (confirmed by ear on this
    hardware; see play_file()'s docstring for why that function exists at
    all). First used this way by potion_play.py's heat-level-aware version
    of this same trick; this is the simpler fixed-volume case, for a caller
    that just wants ambience playing straight through its voice lines with
    no live volume changes. Idempotent, same as start_bubbling().
    """
    global _bg_bubble_thread, _bg_bubble_stop
    if _bg_bubble_thread is not None:
        return
    path = _ensure_bg_bubble_audio(volume)
    stop = threading.Event()
    _bg_bubble_stop = stop

    def _run() -> None:
        process: subprocess.Popen[bytes] | None = None
        while not stop.is_set():
            if (process is None) or (process.poll() is not None):
                process = subprocess.Popen(
                    ["aplay", "-D", ALSA_DEVICE, "-q", str(path)]
                )
            time.sleep(0.1)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    _bg_bubble_thread = threading.Thread(target=_run, daemon=True)
    _bg_bubble_thread.start()


def stop_bubbling_background() -> None:
    """Stop the subprocess-based bubbling loop. Safe to call any time."""
    global _bg_bubble_thread, _bg_bubble_stop
    if _bg_bubble_thread is None:
        return
    assert _bg_bubble_stop is not None  # Set together with _bg_bubble_thread.
    _bg_bubble_stop.set()
    _bg_bubble_thread.join(timeout=2)
    _bg_bubble_thread = _bg_bubble_stop = None


_INTERRUPT_POLL_SEC = 0.02


def play_file(
    path: str | Path,
    blocking: bool = True,
    volume: float | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Play an mp3 (via mpg123) or wav (via aplay).

    ``volume`` defaults to VOICE_VOLUME; the live show passes each witch's own
    WITCH_VOLUMES entry instead, since some voices render quieter than others.

    Blocks until playback finishes unless ``blocking`` is False. pygame's SDL
    mixer causes audible stutter playing TTS output on this hardware even
    though the file itself is clean (confirmed with mpg123 on the same
    speakers), so voice lines are handed off to the command-line players
    instead. mpg123's -f scale (32768 = full volume) gives mp3 playback the
    same volume control pygame did; aplay has no such flag, so wav playback
    (only ever the CLI's own default bubbling smoke test, not a real TTS
    path) plays at full volume.

    Simply running the player alongside an initialized pygame mixer still
    glitches: the mixer holds the ALSA device open even while silent, and the
    two processes fight over it. So when ``blocking`` (the only case actually
    used -- see cauldron_controller.recite_spell), the mixer is released for
    the duration of playback and reopened after. There is no live ambience to
    interrupt: the controller always stops bubbling before reciting.

    ``should_stop``, if given (only meaningful with ``blocking``), is polled
    every 20ms while the line plays; the instant it returns True, the player
    is killed and this returns early rather than waiting out the rest of the
    line. Used by cauldron_controller's mode-select listing, so a mortal
    pressing a mode button early doesn't have to sit through the rest of the
    witch's sentence first.
    """
    path = Path(path)
    if volume is None:
        volume = VOICE_VOLUME
    if path.suffix.lower() == ".mp3":
        scale = str(round(volume * 32768))
        cmd = ["mpg123", "-q", "-a", ALSA_DEVICE, "-f", scale, str(path)]
    else:
        cmd = ["aplay", "-D", ALSA_DEVICE, "-q", str(path)]

    if not blocking:
        subprocess.Popen(cmd)
        return

    global _bubble_sfx, _laugh_sfx
    was_init = pygame.mixer.get_init() is not None
    if was_init:
        pygame.mixer.quit()
    try:
        if should_stop is None:
            subprocess.run(cmd, check=True)
        else:
            process = subprocess.Popen(cmd)
            interrupted = False
            while process.poll() is None:
                if should_stop():
                    interrupted = True
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break
                time.sleep(_INTERRUPT_POLL_SEC)
            if (not interrupted) and (process.returncode != 0):
                raise subprocess.CalledProcessError(process.returncode, cmd)
    finally:
        if was_init:
            pygame.mixer.init()
            # Sound objects don't survive quit()/init(); drop the cache so
            # _bubble()/_laugh() recreate them (at the current volumes) next use.
            _bubble_sfx = None
            _laugh_sfx = None


def main() -> None:
    global BUBBLE_VOLUME, LAUGH_VOLUME, VOICE_VOLUME

    parser = argparse.ArgumentParser(
        description="Play a sound file (or loop the bubbling SFX) to test audio output."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(BUBBLE_SFX_PATH),
        help=f"Audio file to play (default: {BUBBLE_SFX_PATH}). Ignored if "
        "--role is given.",
    )
    parser.add_argument(
        "--role",
        type=Witch,
        choices=list(Witch),
        help="Play this witch's generated line (generated/<role>.mp3) at her "
        "configured WITCH_VOLUMES level, to check the per-witch balance "
        "set in witches.py. Overrides path; still overridable by "
        "--voice-volume.",
    )
    parser.add_argument(
        "--loop",
        type=float,
        metavar="SECONDS",
        help="Loop the file for this many seconds instead of playing it once.",
    )
    parser.add_argument(
        "--ambience",
        type=float,
        metavar="SECONDS",
        help="Run the real bubbling-plus-laugh ambience (start_bubbling()/"
        "stop_bubbling(), as the live show uses it) for this many seconds, "
        "ignoring path/--loop.",
    )
    parser.add_argument(
        "--bubble-volume",
        type=float,
        metavar="0-1",
        help=f"Bubbling volume for --ambience (default: {BUBBLE_VOLUME:g}).",
    )
    parser.add_argument(
        "--laugh-volume",
        type=float,
        metavar="0-1",
        help=f"Laugh volume for --ambience (default: {LAUGH_VOLUME:g}).",
    )
    parser.add_argument(
        "--voice-volume",
        type=float,
        metavar="0-1+",
        help=f"Playback volume when playing path directly, via play_file() as "
        f"the live show does for witch lines (default: {VOICE_VOLUME:g}). "
        f"Unlike --bubble-volume/--laugh-volume, this isn't capped at 1 -- "
        f"it's a real amplification factor on mpg123's decoder, so values "
        f"above 1 genuinely boost volume past the source's original level "
        f"(at the cost of possible clipping/distortion if pushed too far).",
    )
    args = parser.parse_args()

    if args.bubble_volume is not None:
        BUBBLE_VOLUME = args.bubble_volume
    if args.laugh_volume is not None:
        LAUGH_VOLUME = args.laugh_volume
    if args.voice_volume is not None:
        VOICE_VOLUME = args.voice_volume

    setup()
    if args.ambience is not None:
        print(
            f"Running the bubbling (volume={BUBBLE_VOLUME:g}) + laugh "
            f"(volume={LAUGH_VOLUME:g}) ambience for {args.ambience:g}s."
        )
        start_bubbling()
        time.sleep(args.ambience)
        stop_bubbling()
    elif args.loop is not None:
        print(f"Looping {args.path} for {args.loop:g}s.")
        sfx = pygame.mixer.Sound(args.path)
        sfx.play(loops=-1)
        time.sleep(args.loop)
        sfx.stop()
    elif args.role is not None:
        path = GENERATED_DIR / f"{args.role}.mp3"
        volume = WITCH_VOLUMES[args.role] if args.voice_volume is None else VOICE_VOLUME
        print(f"Playing {path} as {args.role} (voice volume={volume:g}).")
        play_file(path, volume=volume)
    else:
        print(f"Playing {args.path} (voice volume={VOICE_VOLUME:g}).")
        play_file(args.path)
    print("Done.")


if __name__ == "__main__":
    main()
