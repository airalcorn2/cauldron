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
import time
from pathlib import Path

import pygame

from witches import WITCHES, Witch

# The 3.5mm jack. ALSA's bare "default" device is unreliable on this Pi --
# with the HDMI outputs and the USB webcam's audio both also present as sound
# cards, it can resolve to one of those instead, which fails to open.
ALSA_DEVICE = "plughw:Headphones,0"

ASSETS_DIR = Path("assets")
GENERATED_DIR = Path("generated")  # Where voice_generator.py writes <witch>.mp3.
# Bubbling sound effect from Pixabay:
# https://pixabay.com/sound-effects/household-cauldron-boiling-173607/
BUBBLE_SFX_PATH = ASSETS_DIR / "bubbling.wav"
# Witch laughing sound effect from Pixabay:
# https://pixabay.com/sound-effects/people-witch-laugh-28961/
LAUGH_SFX_PATH = ASSETS_DIR / "witch-laugh.wav"

# Relative volumes (0.0-1.0) for the two ambience loops. pygame can only scale
# a Sound down, never up, so the laugh -- measured at roughly 7x bubbling's
# RMS level in the source files -- is turned way down rather than trying to
# boost bubbling past its natural volume. Tune with --bubble-volume/
# --laugh-volume on the --ambience test below rather than guessing blind.
BUBBLE_VOLUME: float = 1.0
LAUGH_VOLUME: float = 0.3

# Default witch-line (TTS) playback volume, used by the CLI test below when
# no specific witch is given. The live show instead uses WITCH_VOLUMES, so
# each witch can be balanced independently -- some voices render quieter than
# others at the same ElevenLabs settings. Applied via mpg123's -f scale
# (32768 = full volume); see play_file().
VOICE_VOLUME: float = 0.3

WITCH_VOLUMES: dict[Witch, float] = {w: p.volume for w, p in WITCHES.items()}

_bubble_sfx: pygame.mixer.Sound | None = None
_laugh_sfx: pygame.mixer.Sound | None = None


def setup() -> None:
    """Initialize the pygame mixer. Call once at startup."""
    pygame.mixer.init()


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


def start_bubbling() -> None:
    """Start the bubbling cauldron loop, with a witch's laugh looping over it.

    No-op if bubbling is already playing. The laugh runs on its own mixer
    channel so it overlaps the bubbling loop rather than interrupting it.
    """
    sfx = _bubble()
    if sfx.get_num_channels() == 0:
        sfx.play(loops=-1)
        _laugh().play(loops=-1)


def stop_bubbling() -> None:
    """Stop the bubbling loop and any still-playing laugh.

    Safe to call even if neither ever started.
    """
    _bubble().stop()
    _laugh().stop()


def play_file(
    path: str | Path, blocking: bool = True, volume: float | None = None
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
        subprocess.run(cmd, check=True)
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
    if args.ambience:
        print(
            f"Running the bubbling (volume={BUBBLE_VOLUME:g}) + laugh "
            f"(volume={LAUGH_VOLUME:g}) ambience for {args.ambience:g}s."
        )
        start_bubbling()
        time.sleep(args.ambience)
        stop_bubbling()
    elif args.loop:
        print(f"Looping {args.path} for {args.loop:g}s.")
        sfx = pygame.mixer.Sound(args.path)
        sfx.play(loops=-1)
        time.sleep(args.loop)
        sfx.stop()
    elif args.role:
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
