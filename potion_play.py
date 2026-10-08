"""Controller mini-mode: sprinkle, stir, and heat a potion -- no game, just lights.

Face buttons sprinkle a colored "ingredient" into the potion, which
accumulates permanently -- nothing here fades on its own, with two
exceptions: the stir impulse (talons), which is explicitly meant to
settle, and the comet (D-pad right), which clears tint as it sweeps
around. D-pad left adds a rainbow -- every pixel at once, instead of a
random subset like a normal sprinkle. D-pad up/down steps the heat
through three levels (none/medium/vigorous bubbling), each with its own
bubble-pop rate and bubbling volume -- discrete rather than a continuous
dial, so each level's look and sound can be tuned and checked on its own.
Runs until Select+Start is pressed again.

The ambient bubbling sound plays continuously, including right through
ingredient/heat/stir/rainbow/comet callouts -- unlike the rest of the show, which always
silences its ambience before a voice line (see audio.play_file()'s
docstring). That's because this mode's ambience runs as its own aplay
subprocess rather than through pygame.mixer, so it never needs to be
released for play_file()'s mpg123 call to use the ALSA device; two plain
subprocesses sharing it directly turned out to work cleanly on this
hardware, confirmed by ear, even though pygame's mixer and a subprocess
fighting over it do not (that's the whole reason play_file() exists).

Run directly to play against real hardware:

    .venv/bin/python potion_play.py
"""

from __future__ import annotations

import argparse
import asyncio
import colorsys
import math
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pygame

import audio
import controller
import light_control
from light_control import RGB
from voice_generator import VOICES, VoiceProfile, generate_to_path
from witches import WITCHES, Witch

TICK_SEC = 0.05  # 20Hz.

CACHE_DIR = Path("generated/potion_play")

# Mirrors cauldron_controller.BUTTON_COLORS for button_0-3 -- duplicated
# rather than imported, same as brew_game.py's button indices, to keep
# these mini-modes free of any dependency on the orchestrator.
_FACE_COLORS: dict[int, tuple[str, RGB]] = {
    0: ("blue", RGB(30, 90, 255)),
    1: ("red", RGB(220, 20, 20)),
    2: ("yellow", RGB(230, 200, 0)),
    3: ("green", RGB(60, 200, 40)),
}
_LEFT_TALON = 4
_RIGHT_TALON = 5

INGREDIENTS_PER_COLOR = 6

# Tuning, found by feel.
SPRINKLE_FRACTION = 0.4  # Fraction of pixels hit per sprinkle.
SPRINKLE_INCREMENT = 150.0  # Per-channel add per sprinkle.
# Raw tint magnitude (a pixel's peak channel) at which the white shimmer is
# about 2/3 replaced by the sprinkled color -- see render().
TINT_SATURATION_SCALE = 100.0
# A fully-sprinkled pixel's peak channel normalizes to this, not 255 -- see
# render(). Reserves (255 - this) of real brightness headroom so a pop has
# somewhere to visibly go even on an already fully-colored pixel.
BASE_PEAK_BRIGHTNESS = 130.0
STIR_IMPULSE = 1.5  # Added to spin per talon tap.
STIR_DECAY_PER_SEC = 1.5  # How fast spin relaxes toward 0.
# Heat is three discrete, named levels (up/down tap through them, same as
# rainbow/comet's left/right) rather than a continuous dial -- easier to
# debug "does it look right at each level" than tuning one continuous rate.
HEAT_LEVELS = 3
DEFAULT_HEAT = 0  # No bubbling until the mortal asks for it.
POP_RATES_PER_SEC = (0.0, 30.0, 85.0)  # Expected pops/sec across the ring, per level.
POP_LIFETIME_SEC = 0.4
POP_BOOST = 180.0
POP_RADIUS = 3  # Neighboring pixels catch some of a pop's boost too, falling off with distance -- a single lit pixel among 144 barely reads as a "pop" otherwise.

RAINBOW_MAGNITUDE = 300.0  # Per-pixel tint magnitude added by a rainbow, well past TINT_SATURATION_SCALE so it reads at full color immediately.
COMET_LOOP_SEC = 2.0  # Time for the comet to sweep once around the whole ring.
COMET_TRAIL_LENGTH = 12  # Pixels behind the comet's leading edge that still glow, fading out.
COMET_BOOST = 220.0  # Brightness boost at the comet's leading edge.

_PIXEL_COUNT = light_control.STRIP_CONFIG.count

# A fixed per-pixel phase offset for the idle shimmer, sampled once at
# import time (not fresh each frame -- see brew_game.py's speckle texture
# for why). Each pixel twinkles on its own schedule, in place; nothing here
# rotates, which is what keeps the idle shimmer "non-spinning."
_rng = random.Random(1)
_SHIMMER_PHASE = [_rng.random() for _ in range(_PIXEL_COUNT)]
SHIMMER_HZ = 0.35
SHIMMER_MIN = 90.0
SHIMMER_MAX = 170.0


@dataclass
class _Pop:
    """A short-lived brightness flash at one pixel -- a rising bubble."""

    pixel: int
    age: float = 0.0


@dataclass
class _Comet:
    """A single bright point sweeping once around the ring, clearing tint
    as it passes. ``position`` is a continuous pixel index (0 to
    _PIXEL_COUNT); the comet is done once it wraps past the far end."""

    position: float = 0.0


@dataclass
class PotionPlayState:
    """The potion's full, persistent state.

    Unlike brew_game.PotionState, this is a plain mutable object rather
    than an immutable snapshot: ``tint`` is a 144-entry buffer that only
    ever grows (sprinkles add to it, nothing subtracts), so rebuilding it
    fresh every tick would be pure waste. ``pops`` and ``comet`` are the
    genuinely transient pieces -- short-lived effects, not potion state.
    """

    tint: list[list[float]] = field(
        default_factory=lambda: [[0.0, 0.0, 0.0] for _ in range(_PIXEL_COUNT)]
    )
    heat: int = DEFAULT_HEAT
    spin: float = 0.0
    phase: float = 0.0
    pops: list[_Pop] = field(default_factory=list)
    comet: _Comet | None = None


def sprinkle(state: PotionPlayState, button_rgb: RGB) -> None:
    """Add ``button_rgb`` to a random subset of pixels. Mutates
    ``state.tint`` in place.

    Deliberately unbounded -- unlike an early version, nothing here clamps
    at 255. Channel values only ever grow, by design (nothing fades except
    the comet, see below), and additive color mixing trends toward white
    the more colors pile onto a pixel, so clamping each channel
    independently just washed everything out to white/pale eventually, no
    matter what was actually sprinkled. render() normalizes by each
    pixel's own peak channel instead, which keeps the true hue at full
    saturation forever, regardless of magnitude.
    """
    hit_count = max(1, round(_PIXEL_COUNT * SPRINKLE_FRACTION))
    for i in random.sample(range(_PIXEL_COUNT), hit_count):
        pixel = state.tint[i]
        pixel[0] += SPRINKLE_INCREMENT * (button_rgb.r / 255)
        pixel[1] += SPRINKLE_INCREMENT * (button_rgb.g / 255)
        pixel[2] += SPRINKLE_INCREMENT * (button_rgb.b / 255)


def add_rainbow(state: PotionPlayState) -> None:
    """Add a full-ring rainbow gradient to every pixel's tint at once,
    instead of a random subset like a normal sprinkle. Mutates
    ``state.tint`` in place; unbounded, same as sprinkle()."""
    for i in range(_PIXEL_COUNT):
        hue = i / _PIXEL_COUNT
        r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        pixel = state.tint[i]
        pixel[0] += RAINBOW_MAGNITUDE * r
        pixel[1] += RAINBOW_MAGNITUDE * g
        pixel[2] += RAINBOW_MAGNITUDE * b


def _advance(state: PotionPlayState, dt: float) -> None:
    """Advance everything that changes over time on its own: spin decay,
    the bubble-pop lifecycle, and the comet's sweep (if one is active).
    Mutates ``state`` in place. Heat has no such passive change -- it's a
    discrete level, adjusted directly by up/down taps in run()."""
    state.spin -= state.spin * min(1.0, STIR_DECAY_PER_SEC * dt)
    state.phase = (state.phase + state.spin * dt) % 1.0

    expected_pops = POP_RATES_PER_SEC[state.heat] * dt
    spawn_count = int(expected_pops)
    if random.random() < (expected_pops - spawn_count):
        spawn_count += 1
    for _ in range(spawn_count):
        state.pops.append(_Pop(pixel=random.randrange(_PIXEL_COUNT)))

    survivors = []
    for pop in state.pops:
        pop.age += dt
        if pop.age < POP_LIFETIME_SEC:
            survivors.append(pop)
    state.pops = survivors

    if state.comet is not None:
        speed = _PIXEL_COUNT / COMET_LOOP_SEC
        old_pos = state.comet.position
        new_pos = old_pos + speed * dt
        # Clear every pixel the comet has swept across since the last
        # tick, not just its new position -- at this speed it can cross
        # several pixels in one tick.
        for p in range(int(old_pos), int(new_pos) + 1):
            state.tint[p % _PIXEL_COUNT] = [0.0, 0.0, 0.0]
        if new_pos >= _PIXEL_COUNT:
            state.comet = None
        else:
            state.comet.position = new_pos


def render(state: PotionPlayState, t: float, count: int | None = None) -> list[RGB]:
    """The ring's pixel colors for this state and wall-clock time ``t``.

    Layers: the white shimmer (per-pixel, time-driven, never rotates), the
    sprinkled tint (rotated by ``phase`` so stirring moves the colors
    around), and any active bubble pops or an active comet (both transient
    brightening, via the same pop_boost array).
    """
    if count is None:
        count = _PIXEL_COUNT
    offset = round(state.phase * count)
    pop_boost = [0.0] * count
    for pop in state.pops:
        if pop.pixel >= count:
            continue
        intensity = POP_BOOST * (1.0 - pop.age / POP_LIFETIME_SEC)
        for delta in range(-POP_RADIUS, POP_RADIUS + 1):
            falloff = 1.0 - abs(delta) / (POP_RADIUS + 1)
            pop_boost[(pop.pixel + delta) % count] += intensity * falloff
    if state.comet is not None:
        # Rendered pixel i shows raw tint index (i - offset) % count (see
        # the main loop below), so the comet's own highlight needs the
        # same correction -- otherwise, while stirring, the bright marker
        # would drift away from the actual spot it's clearing.
        lead = (int(state.comet.position) + offset) % count
        for delta in range(COMET_TRAIL_LENGTH):
            falloff = 1.0 - delta / COMET_TRAIL_LENGTH
            pop_boost[(lead - delta) % count] += COMET_BOOST * falloff

    colors: list[RGB] = []
    for i in range(count):
        shimmer = SHIMMER_MIN + (SHIMMER_MAX - SHIMMER_MIN) * (
            0.5 + 0.5 * math.sin(2 * math.pi * (t * SHIMMER_HZ + _SHIMMER_PHASE[i]))
        )
        tint = state.tint[(i - offset) % _PIXEL_COUNT]
        boost = pop_boost[i]
        # tint accumulates unboundedly (see sprinkle()), so it's normalized
        # here by its own peak channel -- preserving the true hue ratio
        # exactly, always at full saturation, regardless of how much raw
        # magnitude has piled up. Normalized to BASE_PEAK_BRIGHTNESS rather
        # than all the way to 255: a pop brightens by scaling this base up
        # further (see below), and if the base were already at 255 there'd
        # be no headroom left for that to show at all.
        # tint_strength (how much the shimmer fades out in favor of that
        # saturated color) grows smoothly from that same magnitude instead,
        # via an asymptotic curve that approaches but never quite reaches
        # "fully colored" -- so there's no hard threshold and nothing to
        # ever get stuck at a bad extreme.
        peak = max(tint)
        if peak > 0.0:
            tint_strength = 1.0 - math.exp(-peak / TINT_SATURATION_SCALE)
            normalized = [BASE_PEAK_BRIGHTNESS * c / peak for c in tint]
        else:
            tint_strength = 0.0
            normalized = [0.0, 0.0, 0.0]
        white = shimmer * (1.0 - tint_strength)
        base_r = white + normalized[0] * tint_strength
        base_g = white + normalized[1] * tint_strength
        base_b = white + normalized[2] * tint_strength
        # A pop brightens this pixel's own color rather than adding a flat
        # boost to every channel equally -- the latter is literally adding
        # white light, which washed everything toward white at high heat
        # (lots of overlapping pops) instead of looking like the potion's
        # own color bubbling. Scaling all three channels by the same
        # factor preserves the hue; only brightness changes.
        pop_scale = 1.0 + boost / 255.0
        r, g, b = base_r * pop_scale, base_g * pop_scale, base_b * pop_scale
        # boost is an unbounded sum of overlapping pops, so this can push a
        # channel past 255 -- clamping each channel independently would
        # clip the brightest one while the others kept climbing underneath
        # it, distorting the hue right when a pop is brightest. Rescaling
        # all three together instead keeps the ratio intact even when the
        # pop is bright enough to hit the ceiling.
        peak_final = max(r, g, b)
        if peak_final > 255.0:
            rescale = 255.0 / peak_final
            r *= rescale
            g *= rescale
            b *= rescale
        colors.append(RGB(round(r), round(g), round(b)))
    return colors


def _ingredient_path(witch: Witch, key: str) -> Path:
    return CACHE_DIR / f"{witch}_ingredient_{key}.mp3"


def _heat_path(witch: Witch, level: int) -> Path:
    return CACHE_DIR / f"{witch}_heat_{level}.mp3"


def _stir_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_stir.mp3"


def _rainbow_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_rainbow.mp3"


def _comet_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_comet.mp3"


def ensure_cache() -> None:
    """Synthesize any missing potion-play voice lines, so sprinkling,
    heat, stirring, and the rainbow/comet ingredients are all instant.

    Mirrors cauldron_controller._ensure_mode_select_cache(): collect what's
    missing, then synthesize it once, sequentially (ElevenLabs rejects a
    burst of concurrent requests). Called once from
    cauldron_controller.main(), alongside that function.
    """
    missing: list[tuple[str, VoiceProfile, Path]] = []
    for witch in Witch:
        profile = WITCHES[witch]
        for key, text in profile.potion_ingredients.items():
            path = _ingredient_path(witch, key)
            if not path.exists():
                missing.append((text, VOICES[witch], path))
        for level, text in enumerate(profile.potion_heat):
            path = _heat_path(witch, level)
            if not path.exists():
                missing.append((text, VOICES[witch], path))
        rainbow_path = _rainbow_path(witch)
        if not rainbow_path.exists():
            missing.append((profile.potion_rainbow, VOICES[witch], rainbow_path))
        comet_path = _comet_path(witch)
        if not comet_path.exists():
            missing.append((profile.potion_comet, VOICES[witch], comet_path))
        stir_path = _stir_path(witch)
        if not stir_path.exists():
            missing.append((profile.potion_stir, VOICES[witch], stir_path))

    if len(missing) == 0:
        return
    print(f"Synthesizing {len(missing)} potion-play voice line(s).")

    async def _generate_all() -> None:
        for text, profile, path in missing:
            await generate_to_path(text, profile, path)

    asyncio.run(_generate_all())
    print("Potion-play cache ready.")


_bubble_thread: threading.Thread | None = None
_bubble_stop: threading.Event | None = None

# One relative volume per heat level -- aplay has no live volume control
# (unlike mpg123's -f, used elsewhere), so each level is instead a
# separately pre-rendered, pre-scaled copy of the SFX (see
# _ensure_bubble_audio()), and the loop just picks which file to play.
BUBBLE_VOLUMES_PER_HEAT = (0.2, 0.55, 1.0)


def _bubble_audio_path(level: int) -> Path:
    return CACHE_DIR / f"bubbling_heat_{level}.wav"


def _ensure_bubble_audio() -> None:
    """Pre-render one volume-scaled copy of the bubbling SFX per heat
    level, via ffmpeg, if not already on disk. One-time, near-instant."""
    for level, volume in enumerate(BUBBLE_VOLUMES_PER_HEAT):
        path = _bubble_audio_path(level)
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(audio.BUBBLE_SFX_PATH),
                "-filter:a", f"volume={volume}",
                str(path),
            ],
            check=True,
        )


def _start_bubbling_bg(state: PotionPlayState) -> None:
    """Loop the bubbling SFX as its own aplay subprocess, independent of
    pygame.mixer, so voice lines can play right over it -- see this
    module's docstring. Idempotent, same as audio.start_bubbling().

    The SFX file itself is ~28s long, so checking ``state.heat`` only once
    per playthrough (the first version of this) could take up to 28s to
    make a volume change audible. Checking every 0.1s instead and
    restarting aplay immediately on any change makes it near-instant;
    aplay can't change the volume of audio it's already playing, so a
    changed level always means killing the current process and starting
    the newly-selected one over from its beginning.
    """
    global _bubble_thread, _bubble_stop
    if _bubble_thread is not None:
        return
    stop = threading.Event()
    _bubble_stop = stop

    def _run() -> None:
        current_level = -1
        process: subprocess.Popen[bytes] | None = None
        while not stop.is_set():
            finished = (process is not None) and (process.poll() is not None)
            if (state.heat != current_level) or finished:
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                current_level = state.heat
                process = subprocess.Popen(
                    ["aplay", "-D", audio.ALSA_DEVICE, "-q", str(_bubble_audio_path(current_level))]
                )
            time.sleep(0.1)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

    _bubble_thread = threading.Thread(target=_run, daemon=True)
    _bubble_thread.start()


def _stop_bubbling_bg() -> None:
    """Stop the background bubbling loop. Safe to call any time."""
    global _bubble_thread, _bubble_stop
    if _bubble_thread is None:
        return
    assert _bubble_stop is not None  # Set together with _bubble_thread.
    _bubble_stop.set()
    _bubble_thread.join(timeout=2)
    _bubble_thread = _bubble_stop = None


def run(*, select_button: int = 8, start_button: int = 9) -> None:
    """Free play until Select+Start is pressed again. Blocks until then.

    Like brew_game.run(), checks the combo itself each tick rather than
    relying on the shared _mode_switch_abort Event, which is already set
    from the combo press that opened mode selection in the first place.

    The animation (spin decay, bubble pops, rendering) runs on its own
    background thread rather than this function's own loop, same reasoning
    as the bubbling sound's background thread: audio.play_file() for each
    voice line blocks for a second or two, and without a separate thread
    the whole ring would visibly freeze for that long on every callout.
    The two threads share ``state`` with no locking -- same informal,
    good-enough-for-a-visual-toy concurrency already used elsewhere in this
    project (e.g., light_control's flicker/rainbow threads); a rare
    half-applied update to a single float is imperceptible here.

    Also quits pygame's mixer for the whole session (restored on exit),
    rather than leaving play_file() to quit and reinitialize it around
    every single voice line as it normally does: nothing here ever uses
    the mixer (bubbling is its own subprocess, not a pygame.mixer.Sound),
    so that per-line dance is pure wasted latency -- worse, exactly the
    kind of latency that could leave stale audio briefly overlapping the
    next line during a fast run of interrupting presses.
    """
    state = PotionPlayState()
    start_time = time.monotonic()
    animate_stop = threading.Event()
    mixer_was_init = pygame.mixer.get_init() is not None
    if mixer_was_init:
        pygame.mixer.quit()

    def animate() -> None:
        while not animate_stop.is_set():
            _advance(state, TICK_SEC)
            light_control.set_pixels(render(state, time.monotonic() - start_time))
            animate_stop.wait(TICK_SEC)

    def speak(path: Path, witch: Witch) -> tuple[bool, str | None]:
        """Play a voice line, but cut it short the instant anything else is
        pressed, rather than making the mortal wait it out -- a flurry of
        quick presses then only ever finishes narrating the *last* one.
        Returns (should_exit, interrupting_press): the interrupting press
        is handed back rather than dropped, so the caller still acts on it
        (it's already been drained from the event queue by check() below,
        so polling again would just miss it).
        """
        next_pressed: str | None = None
        should_exit = False

        def check() -> bool:
            nonlocal next_pressed, should_exit
            if controller.is_held(select_button) and controller.is_held(start_button):
                should_exit = True
                return True
            pressed = controller.poll_pressed()
            if pressed is not None:
                next_pressed = pressed
                return True
            return False

        audio.play_file(path, volume=audio.WITCH_VOLUMES[witch], should_stop=check)
        return should_exit, next_pressed

    _ensure_bubble_audio()
    _start_bubbling_bg(state)
    animate_thread = threading.Thread(target=animate, daemon=True)
    animate_thread.start()
    pending: str | None = None
    try:
        while True:
            if pending is None:
                pending = controller.poll_pressed()  # Also keeps is_held() current.
            if controller.is_held(select_button) and controller.is_held(start_button):
                return

            pressed, pending = pending, None
            if pressed is not None:
                # Every press is announced by a freshly, independently chosen
                # witch -- not one witch for the whole session -- so the cast
                # feels alive throughout a round, same as mode-select's "a
                # random witch asks" pattern.
                witch = random.choice(list(Witch))
                voice_path: Path | None = None
                if pressed in ("button_0", "button_1", "button_2", "button_3"):
                    index = int(pressed.removeprefix("button_"))
                    color, button_rgb = _FACE_COLORS[index]
                    sprinkle(state, button_rgb)
                    key = f"{color}_{random.randrange(INGREDIENTS_PER_COLOR)}"
                    voice_path = _ingredient_path(witch, key)
                elif pressed == f"button_{_LEFT_TALON}":
                    state.spin -= STIR_IMPULSE
                    voice_path = _stir_path(witch)
                elif pressed == f"button_{_RIGHT_TALON}":
                    state.spin += STIR_IMPULSE
                    voice_path = _stir_path(witch)
                elif pressed == "left":
                    add_rainbow(state)
                    voice_path = _rainbow_path(witch)
                elif pressed == "right":
                    state.comet = _Comet()  # Restarts from the top if one's already active.
                    voice_path = _comet_path(witch)
                elif pressed == "up":
                    state.heat = min(HEAT_LEVELS - 1, state.heat + 1)
                    voice_path = _heat_path(witch, state.heat)
                elif pressed == "down":
                    state.heat = max(0, state.heat - 1)
                    voice_path = _heat_path(witch, state.heat)

                if voice_path is not None:
                    should_exit, pending = speak(voice_path, witch)
                    if should_exit:
                        return

            time.sleep(TICK_SEC)
    finally:
        animate_stop.set()
        animate_thread.join(timeout=2)
        _stop_bubbling_bg()
        light_control.leds_off()
        if mixer_was_init:
            pygame.mixer.init()


def main() -> None:
    argparse.ArgumentParser(
        description="Sprinkle, stir, and heat a potion -- no game, just lights."
    ).parse_args()
    light_control.setup()
    audio.setup()
    if not controller.setup():
        raise SystemExit("No gamepad connected.")
    ensure_cache()

    print(
        "Face buttons (blue/red/yellow/green) sprinkle that color in. D-pad "
        "left adds a rainbow; D-pad right sends a comet around, clearing "
        "colors as it passes. D-pad up/down steps the heat through 3 "
        "levels. Talons (button_4/button_5) stir. Select+Start to quit."
    )
    run()
    print("Done.")


if __name__ == "__main__":
    main()
