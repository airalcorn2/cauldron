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


def wait_for_quiet(settle: float, pin: int = IR_SENSOR_PIN) -> None:
    """Wait until the beam has been clear for ``settle`` continuous seconds.

    Call right after a triggering break to give a moment for more objects to
    land before acting on the tray: any further break resets the settle
    clock. No timeout: fine for a prop that is watched rather than left fully
    unattended. Call with a deadline of your own (or just interrupt the
    process) if that ever stops being true.
    """
    last_broken = time.monotonic()
    while True:
        if beam_broken(pin):
            last_broken = time.monotonic()
        if time.monotonic() - last_broken >= settle:
            return
        time.sleep(_POLL_SEC)


def _await_clear(
    pin: int,
    debounce: float,
    *,
    hold: float | None = None,
    on_hold: Callable[[], None] | None = None,
) -> float:
    """Block until the beam has been clear for ``debounce`` continuous seconds.

    Brief re-breaks (sensor bounce as an object tumbles past) do not end the
    wait. Returns how long the beam was obstructed, measured from the first
    sample to the last break seen.

    If ``hold`` is given, ``on_hold`` fires the instant the obstruction --
    while still ongoing -- reaches that duration, rather than waiting for it
    to end first.
    """
    start = time.monotonic()
    last_broken = start
    hold_fired = False
    while True:
        if beam_broken(pin):
            last_broken = time.monotonic()
            if (
                hold is not None
                and not hold_fired
                and last_broken - start >= hold
            ):
                hold_fired = True
                if on_hold is not None:
                    on_hold()
        elif time.monotonic() - last_broken >= debounce:
            return last_broken - start
        time.sleep(_POLL_SEC)


def wait_for_drops(
    settle: float,
    *,
    hold: float = 1.2,
    clear_debounce: float = 0.12,
    on_obstruct: Callable[[], None] | None = None,
    on_drop: Callable[[], None] | None = None,
    on_repeat: Callable[[], None] | None = None,
    pin: int = IR_SENSOR_PIN,
) -> None:
    """Wait for items to be dropped in, then for the beam to go quiet.

    Unlike react/story mode, nothing has been dropped yet when request mode
    starts this wait, so it blocks for the first break itself rather than
    expecting the caller to have just seen one. Obstructing the beam for
    ``hold`` seconds or more -- at any point, not just before the first real
    drop -- is a "repeat the request" gesture instead of a drop: ``on_repeat``
    fires the instant that threshold is reached, while the beam is still
    held, rather than waiting for the mortal to let go first. A quick
    pass-through, below ``hold``, calls ``on_drop`` instead once it ends, and
    (like any further drop) resets the settle clock.

    There is no way to tell which one a break will turn out to be until
    either the hold threshold or a release happens, so ``on_obstruct`` fires
    immediately on *any* break, before it is classified at all -- in time to
    silence ambience before it could possibly turn into a hold, rather than
    leaving it running and only cutting it after the fact. No timeout, for
    the same reason as ``wait_for_quiet``.
    """
    started = False
    settled_since: float | None = None

    while True:
        now = time.monotonic()
        if started:
            if settled_since is None:
                settled_since = now
            elif now - settled_since >= settle:
                return

        if beam_broken(pin):
            settled_since = None
            if on_obstruct is not None:
                on_obstruct()
            obstructed = _await_clear(
                pin, clear_debounce, hold=hold, on_hold=on_repeat
            )
            if obstructed < hold:
                started = True
                if on_drop is not None:
                    on_drop()
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
