"""LED ring lighting effects for the cauldron.

Run directly to exercise the hardware:

    .venv/bin/python light_control.py leds                  # Cycle red, green, blue, then off.
    .venv/bin/python light_control.py color hazel           # Hold a solid witch color.
    .venv/bin/python light_control.py color 40 150 90       # Hold an explicit R G B.
    .venv/bin/python light_control.py --brightness 40 color hazel  # Dimmer.
    .venv/bin/python light_control.py flicker               # Run the wait-for-API strobe.

The ring is driven over SPI (GPIO 10 / MOSI) rather than PWM, so no root is
required: SPI must be enabled and the core clock pinned (see
docs/step-8-led-ring.md), and access comes from the `spi` group
membership.
"""

from __future__ import annotations

import argparse
import colorsys
import random
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

from rpi_ws281x import Color, PixelStrip

from witches import WITCHES, Witch


@dataclass(frozen=True, slots=True)
class RGB:
    """An 8-bit-per-channel color."""

    r: int
    g: int
    b: int

    def scaled(self, factor: float) -> RGB:
        """Return this color with every channel multiplied by ``factor``."""
        return RGB(int(self.r * factor), int(self.g * factor), int(self.b * factor))

    def packed(self) -> int:
        """Pack into the 24-bit integer the rpi_ws281x API expects."""
        return Color(self.r, self.g, self.b)


BLACK = RGB(0, 0, 0)
GREEN = RGB(60, 200, 40)  # Success shimmer.
RED = RGB(220, 20, 20)  # Failure fade.
GOLD = RGB(255, 190, 30)  # victory_swirl()'s comet color.
SPARKLE = RGB(255, 255, 255)


@dataclass(frozen=True, slots=True)
class StripConfig:
    """Wiring and timing parameters for the LED strip."""

    count: int = 144
    pin: int = 10  # GPIO 10 / SPI0 MOSI (physical pin 19). SPI mode needs no root.
    freq_hz: int = 800_000
    dma: int = 10  # Ignored in SPI mode; kept for the PixelStrip signature.
    brightness: int = 20
    invert: bool = False
    channel: int = 0

    def create(self) -> PixelStrip:
        """Build and start a PixelStrip from this configuration."""
        strip = PixelStrip(
            self.count,
            self.pin,
            self.freq_hz,
            self.dma,
            self.invert,
            self.brightness,
            self.channel,
        )
        strip.begin()
        return strip


STRIP_CONFIG = StripConfig()

WITCH_COLORS: dict[Witch, RGB] = {w: RGB(*p.color) for w, p in WITCHES.items()}

# Background strobe shown while the cauldron works (capture and vision model).
# It cycles through the three witch colors.
_FLICKER_COLORS: tuple[RGB, ...] = tuple(WITCH_COLORS.values())

_strip: PixelStrip | None = None
_flicker_thread: threading.Thread | None = None
_flicker_stop: threading.Event | None = None
_rainbow_thread: threading.Thread | None = None
_rainbow_stop: threading.Event | None = None
# Whatever set_leds()/set_pixels() last painted, so fade_to_black() can fade
# out of it -- e.g. brew_game.py's live render, left on the ring after a
# round ends (see run()) -- without needing to read pixel state back from
# the strip itself.
_last_colors: list[RGB] = []


def setup() -> None:
    """Initialize the LED strip.

    Safe to call alongside other modules' setup(); calling it twice is harmless.
    """
    global _strip
    if _strip is None:
        _strip = STRIP_CONFIG.create()


def set_leds(color: RGB) -> None:
    """Fill the ring with a single color."""
    global _last_colors
    if _strip is None:
        return  # setup() has not run yet.
    packed = color.packed()
    for i in range(_strip.numPixels()):
        _strip.setPixelColor(i, packed)
    _strip.show()
    _last_colors = [color] * _strip.numPixels()


def set_pixels(colors: Sequence[RGB]) -> None:
    """Paint each pixel its own color in one frame.

    ``colors[i]`` goes to pixel ``i``; any pixels beyond ``len(colors)`` are
    left at whatever they last held. For brew_game.py's per-pixel rendering,
    which (unlike set_leds()) needs a different color at every position
    around the ring rather than one solid fill.
    """
    global _last_colors
    if _strip is None:
        return  # setup() has not run yet.
    for i, color in enumerate(colors):
        _strip.setPixelColor(i, color.packed())
    _strip.show()
    _last_colors = list(colors)


def set_brightness(value: int) -> None:
    """Set the ring's global brightness (0-255) and repaint at the new level."""
    if _strip is None:
        return
    _strip.setBrightness(max(0, min(255, value)))
    _strip.show()


def leds_off() -> None:
    """Blank the ring."""
    set_leds(BLACK)


def celebrate(cycles: int = 6, interval: float = 0.12) -> None:
    """Quick green shimmer for a successful spell."""
    for i in range(cycles):
        set_leds(GREEN if i % 2 == 0 else BLACK)
        time.sleep(interval)
    leds_off()


def fizzle(steps: int = 14, interval: float = 0.07) -> None:
    """Fade red down to nothing for a failed spell."""
    for step in range(steps, -1, -1):
        set_leds(RED.scaled(step / steps))
        time.sleep(interval)
    leds_off()


def fade_to_black(steps: int = 20, interval: float = 0.05) -> None:
    """Fade whatever's currently on the ring down to off, in place.

    Unlike fizzle() (which fades a fixed red, for a judged failure), this
    fades out of whatever set_leds()/set_pixels() last painted -- for
    brew_game.py's failure outcome, that's the brew's own last rendered
    frame, left showing through the witch's line (see brew_game.run())
    rather than snapped to black the instant the round ends.
    """
    start = _last_colors
    if not start:
        leds_off()
        return
    for step in range(steps, -1, -1):
        factor = step / steps
        set_pixels([color.scaled(factor) for color in start])
        time.sleep(interval)
    leds_off()


def victory_swirl(loops: int = 3, trail_length: int = 18) -> None:
    """A one-shot magical flourish for an outright win.

    Deliberately distinct from every other effect in this file: a gold
    comet sweeps the ring with a fading trail and scattered white
    sparkle flecks, accelerating lap over lap, then the whole ring
    flashes gold-white before fading out -- unlike celebrate()'s flat
    green blink (used for request/category's photo-judged wins),
    fizzle()'s flat color fade, start_rainbow()'s steady full-spectrum
    chase, or potion_play.py's comet (a single sweep that clears color
    rather than adding a trail). Reserved for brew_game.py's victory
    outcome.
    """
    if _strip is None:
        return
    count = _strip.numPixels()
    for lap in range(loops):
        interval = 0.03 / (lap + 1)  # Each lap faster than the last.
        for i in range(count):
            colors = [BLACK] * count
            for t in range(trail_length):
                colors[(i - t) % count] = GOLD.scaled(1.0 - t / trail_length)
            for _ in range(2):
                if random.random() < 0.25:
                    colors[random.randrange(count)] = SPARKLE
            set_pixels(colors)
            time.sleep(interval)
    for _ in range(3):
        set_leds(SPARKLE)
        time.sleep(0.08)
        set_leds(GOLD)
        time.sleep(0.08)
    fade_to_black()


def start_flicker(interval: float = 0.08) -> None:
    """Strobe the ring on a background thread until stop_flicker() is called.

    Runs concurrently with the bubbling SFX while the spell is being fetched.
    Idempotent -- a second call while already flickering does nothing.
    """
    global _flicker_thread, _flicker_stop
    if _flicker_thread is not None:
        return
    stop = threading.Event()
    _flicker_stop = stop

    def _run() -> None:
        i = 0
        while not stop.is_set():
            set_leds(_FLICKER_COLORS[i % len(_FLICKER_COLORS)])
            i += 1
            stop.wait(interval)
        leds_off()

    _flicker_thread = threading.Thread(target=_run, daemon=True)
    _flicker_thread.start()


def stop_flicker() -> None:
    """Stop the background strobe and blank the ring. Safe to call any time."""
    global _flicker_thread, _flicker_stop
    if _flicker_thread is None:
        return
    assert _flicker_stop is not None  # Set together with _flicker_thread.
    _flicker_stop.set()
    _flicker_thread.join(timeout=2)
    _flicker_thread = _flicker_stop = None
    leds_off()


def start_rainbow(interval: float = 0.03, step: float = 0.015) -> None:
    """Spin a full rainbow gradient around the ring on a background thread
    until stop_rainbow() is called.

    Unlike start_flicker() (which fills the whole ring with one color at a
    time), every pixel gets its own hue, spread evenly around the ring, so
    the full color wheel is visible at once; each tick rotates every pixel's
    hue forward by ``step``, so the gradient visibly chases around the ring
    rather than just shifting color in place.

    Used as an opening flourish when mode selection begins (see
    cauldron_controller.py), before the ring settles onto each mode's own
    fixed color. Idempotent -- a second call while already running does
    nothing.
    """
    global _rainbow_thread, _rainbow_stop
    if _rainbow_thread is not None:
        return
    stop = threading.Event()
    _rainbow_stop = stop

    def _run() -> None:
        offset = 0.0
        while not stop.is_set():
            if _strip is not None:
                count = _strip.numPixels()
                for i in range(count):
                    hue = (offset + i / count) % 1.0
                    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
                    _strip.setPixelColor(
                        i, Color(round(r * 255), round(g * 255), round(b * 255))
                    )
                _strip.show()
            offset = (offset + step) % 1.0
            stop.wait(interval)
        leds_off()

    _rainbow_thread = threading.Thread(target=_run, daemon=True)
    _rainbow_thread.start()


def stop_rainbow() -> None:
    """Stop the background rainbow sweep and blank the ring. Safe to call any time."""
    global _rainbow_thread, _rainbow_stop
    if _rainbow_thread is None:
        return
    assert _rainbow_stop is not None  # Set together with _rainbow_thread.
    _rainbow_stop.set()
    _rainbow_thread.join(timeout=2)
    _rainbow_thread = _rainbow_stop = None
    leds_off()


def led_flicker(duration: float = 2.0, interval: float = 0.08) -> None:
    """Strobe the ring for a fixed duration, blocking. Used by the CLI test."""
    start_flicker(interval)
    time.sleep(duration)
    stop_flicker()


def _parse_color(values: list[str]) -> RGB:
    """Turn the ``color`` subcommand's argument(s) into an RGB."""
    if len(values) == 1:
        try:
            return WITCH_COLORS[Witch(values[0])]
        except ValueError:
            raise SystemExit(f"unknown witch color: {values[0]!r}")
    if len(values) == 3:
        return RGB(*(int(c) for c in values))
    raise SystemExit("color takes a witch name or three integers 0-255")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exercise the cauldron's LED ring."
    )
    parser.add_argument(
        "--brightness",
        type=int,
        metavar="0-255",
        help=f"Override the strip brightness for this run "
        f"(default: {STRIP_CONFIG.brightness}).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("leds", help="Cycle the ring through red, green, blue, then off.")
    sub.add_parser("flicker", help="Run the wait-for-API strobe effect.")
    color = sub.add_parser(
        "color",
        help="Hold the ring on a witch color name, or an explicit 'R G B' (0-255).",
    )
    color.add_argument(
        "value",
        nargs="+",
        help=f"A witch name ({'/'.join(Witch)}) or three integers 0-255.",
    )
    args = parser.parse_args()

    setup()
    if args.brightness is not None:
        set_brightness(args.brightness)
    try:
        if args.command == "leds":
            for name, rgb in (
                ("Red", RGB(255, 0, 0)),
                ("Green", RGB(0, 255, 0)),
                ("Blue", RGB(0, 0, 255)),
            ):
                print(f"{name}...")
                set_leds(rgb)
                time.sleep(2)
            print("Off.")
            leds_off()
        elif args.command == "flicker":
            led_flicker()
        elif args.command == "color":
            target = _parse_color(args.value)
            print(f"Holding {target}. Press Ctrl+C to blank the ring and exit.")
            set_leds(target)
            try:
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        leds_off()


if __name__ == "__main__":
    main()
