"""IR break-beam sensor on the cauldron's drop chute.

Run directly to watch for beam breaks:

    .venv/bin/python ir_sensor.py
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable

import RPi.GPIO as GPIO

IR_SENSOR_PIN = 17
_POLL_SEC = 0.02


def setup(pin: int = IR_SENSOR_PIN) -> None:
    """Configure the sensor pin. Safe to call alongside other modules' setup()."""
    GPIO.setmode(GPIO.BCM)
    # A prior run that skipped cleanup leaves the pin claimed; suppress the warning.
    GPIO.setwarnings(False)
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def beam_broken(pin: int = IR_SENSOR_PIN) -> bool:
    """Return True while something is interrupting the beam."""
    return GPIO.input(pin) == GPIO.LOW


def _await_clear(pin: int, debounce: float) -> float:
    """Block until the beam has been clear for ``debounce`` continuous seconds.

    Brief re-breaks (sensor bounce as an object tumbles past) do not end the
    wait. Returns how long the beam was obstructed, measured from the first
    sample to the last break seen.
    """
    start = time.monotonic()
    last_broken = start
    while True:
        if beam_broken(pin):
            last_broken = time.monotonic()
        elif time.monotonic() - last_broken >= debounce:
            return last_broken - start
        time.sleep(_POLL_SEC)


def wait_for_quiet(
    settle: float, *, timeout: float, pin: int = IR_SENSOR_PIN
) -> None:
    """Wait until the beam has been clear for ``settle`` continuous seconds.

    Call right after a triggering break to give a moment for more objects to
    land before acting on the tray: any further break resets the settle clock,
    the same way ``collect_breaks`` settles after its last expected drop. Gives
    up after ``timeout`` seconds even if the beam never truly settles, so a
    steady stream of drops can't stall the show forever.
    """
    deadline = time.monotonic() + timeout
    last_broken = time.monotonic()
    while True:
        if beam_broken(pin):
            last_broken = time.monotonic()
        now = time.monotonic()
        if now - last_broken >= settle or now >= deadline:
            return
        time.sleep(_POLL_SEC)


def collect_breaks(
    expected: int,
    *,
    timeout: float,
    settle: float,
    idle_timeout: float,
    hold: float = 1.2,
    clear_debounce: float = 0.12,
    on_break: Callable[[int], None] | None = None,
    on_repeat: Callable[[], None] | None = None,
    pin: int = IR_SENSOR_PIN,
) -> int:
    """Count objects passing through the beam during a collection window.

    A quick break -- something falling through -- counts as one drop, even if
    the sensor bounces during the pass; ``clear_debounce`` seconds of continuous
    clear signal end the event. Obstructing the beam for ``hold`` seconds or
    more is instead a "repeat" gesture: it calls ``on_repeat`` and is not
    counted (a falling object cannot hold the beam that long).

    Counting stops once ``expected`` drops have been seen and the beam has then
    stayed clear for ``settle`` seconds, or when ``timeout`` seconds elapse. If
    nothing passes within ``idle_timeout``, returns 0. ``on_break`` receives the
    running count after each counted drop.
    """
    start = time.monotonic()
    count = 0
    settled_since: float | None = None

    while True:
        now = time.monotonic()
        if now - start > timeout:
            return count
        if count == 0 and now - start > idle_timeout:
            return 0
        if count >= expected:
            if settled_since is None:
                settled_since = now
            elif now - settled_since >= settle:
                return count

        if beam_broken(pin):
            settled_since = None
            obstructed = _await_clear(pin, clear_debounce)
            if obstructed >= hold:
                if on_repeat is not None:
                    on_repeat()
                start = time.monotonic()  # Fresh window after a repeat.
            else:
                count += 1
                if on_break is not None:
                    on_break(count)
        time.sleep(_POLL_SEC)


def watch(pin: int = IR_SENSOR_PIN) -> None:
    """Print a line every time the beam is broken, until interrupted."""
    setup(pin)
    print("Watching for beam breaks. Press Ctrl+C to stop.")
    try:
        while True:
            if beam_broken(pin):
                print("Beam broken!")
                time.sleep(0.5)
            time.sleep(_POLL_SEC)
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.cleanup()


def main() -> None:
    argparse.ArgumentParser(
        description="Watch the IR break-beam sensor and report each break."
    ).parse_args()
    watch()


if __name__ == "__main__":
    main()
