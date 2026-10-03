"""USB gamepad input for live mode switching.

Run directly to watch for button/D-pad presses (useful for mapping a
different pad -- press things and see what name comes back):

    .venv/bin/python controller.py
"""

from __future__ import annotations

import argparse
import time

import pygame

_AXIS_THRESHOLD = 0.5

_joystick: pygame.joystick.JoystickType | None = None
# Last direction fired per D-pad axis, or None while at rest. Tracked so a
# held direction doesn't repeat-fire and releasing it resets for next time.
_axis_state: dict[int, str | None] = {0: None, 1: None}


def setup() -> bool:
    """Initialize the joystick subsystem and open the first gamepad.

    Returns False if none is connected, so the mode-switch feature can
    degrade gracefully (the show still runs, just without live switching)
    rather than crash the whole controller.
    """
    global _joystick
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No USB gamepad found; live mode-switching is disabled.")
        return False
    _joystick = pygame.joystick.Joystick(0)
    _joystick.init()
    print(f"Gamepad connected: {_joystick.get_name()!r}.")
    return True


def _axis_direction(axis: int, value: float) -> str:
    if axis == 0:
        return "left" if value < 0 else "right"
    return "up" if value < 0 else "down"


def is_held(index: int) -> bool:
    """Return whether button ``index`` is currently held down.

    Unlike ``poll_pressed()``, this is a direct state check, not an
    edge-triggered event -- for detecting a simultaneous combo (e.g. two
    buttons held together), where neither button's own press event is
    guaranteed to land in the same poll tick as the other's. Reflects
    whatever ``poll_pressed()`` most recently pumped, so call that first in
    the same loop if you haven't already this tick.
    """
    if _joystick is None:
        return False
    return bool(_joystick.get_button(index))


def poll_pressed() -> str | None:
    """Return the input just pressed (edge-triggered), or None.

    Buttons report as ``"button_<index>"``; the D-pad -- reported as two
    axes on this pad rather than a hat -- reports as "up"/"down"/"left"/
    "right". Pumps the event queue, so call this regularly (once per
    main-loop tick is enough) even when the caller doesn't need the result,
    or events will back up.
    """
    if _joystick is None:
        return None

    pressed: str | None = None
    for event in pygame.event.get():
        if event.type == pygame.JOYBUTTONDOWN:
            pressed = f"button_{event.button}"
        elif (event.type == pygame.JOYAXISMOTION) and (event.axis in _axis_state):
            if abs(event.value) <= _AXIS_THRESHOLD:
                _axis_state[event.axis] = None  # Back at rest; can fire again.
                continue
            direction = _axis_direction(event.axis, event.value)
            if _axis_state[event.axis] != direction:
                _axis_state[event.axis] = direction
                pressed = direction
    return pressed


def main() -> None:
    argparse.ArgumentParser(
        description="Watch the USB gamepad and report each button/D-pad press."
    ).parse_args()
    if not setup():
        return
    print("Press any button or D-pad direction. Press Ctrl+C to stop.")
    try:
        while True:
            pressed = poll_pressed()
            if pressed is not None:
                print(f"Pressed: {pressed}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
