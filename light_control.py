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
import threading
import time
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


def setup() -> None:
    """Initialize the LED strip.

    Safe to call alongside other modules' setup(); calling it twice is harmless.
    """
    global _strip
    if _strip is None:
        _strip = STRIP_CONFIG.create()


def set_leds(color: RGB) -> None:
    """Fill the ring with a single color."""
    if _strip is None:
        return  # setup() has not run yet.
    packed = color.packed()
    for i in range(_strip.numPixels()):
        _strip.setPixelColor(i, packed)
    _strip.show()


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
