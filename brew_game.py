"""Controller mini-game: keep the brew's heat and swirl in balance.

Heat (button_1, red) and cool (button_0, blue) push the potion's temperature;
straying from the sweet spot speeds up the swirl. The talons (button_4/
button_5) both brake the swirl, and each also steers its direction toward
itself. Boil over, freeze, or spin out of control and the brew is lost; hold
steady long enough and it's a win. The ring renders the state live: speckled
colors sampled around a mean hue (blue/green/red for cold/ideal/hot) rotate
around it at the current swirl speed and direction.

Run directly to play one round against real hardware:

    .venv/bin/python brew_game.py                 # Survive the default 30s to win.
    .venv/bin/python brew_game.py --duration 15    # An easier/quicker round.
"""

from __future__ import annotations

import argparse
import colorsys
import random
import time
from dataclasses import dataclass
from typing import Literal

import audio
import controller
import light_control

TICK_SEC = 0.05  # 20Hz.

# Physics tuning, found by feel -- retuned after a real playtest showed doing
# nothing at all failed in ~13s (via rpm alone), barely giving a first-time
# player time to even discover the talon controls before losing. These give
# ~20s of runway before neglect is fatal, while attentive play (managing
# both temp and rpm) still comfortably makes the 30s target.
DRIFT_PER_SEC = 1.2  # Constant upward temperature drift -- it always wants to boil.
HEAT_PER_SEC = 18.0  # heat_up button's effect on temp.
COOL_PER_SEC = 18.0  # cool_down button's effect on temp.
IMBALANCE_TO_RPM = 0.02  # How much straying from 50 speeds up the swirl.
BRAKE_PER_SEC = 2.5  # Talon braking strength on swirl_rpm.
STEER_PER_SEC = 1.5  # Talon steering strength on swirl_dir.
BASELINE_DIR = 0.6  # swirl_dir drifts toward this (not 0) when neither talon is held.
DIR_RELAX_PER_SEC = 0.4  # How fast swirl_dir relaxes toward BASELINE_DIR.
MIN_RPM = 0.2
MAX_RPM = 6.0  # Exceeding this is a loss.
TARGET_SURVIVAL_SEC = 30.0  # Default win condition.

Status = Literal["running", "victory", "failure"]
Outcome = Literal["victory", "failure", "aborted"]

_HEAT_UP_BUTTON = 1  # Red face button.
_COOL_DOWN_BUTTON = 0  # Blue face button.
_STIR_LEFT_BUTTON = 4  # Left talon.
_STIR_RIGHT_BUTTON = 5  # Right talon.


@dataclass(frozen=True, slots=True)
class PotionState:
    """The brew's full state. Immutable -- update() returns a new one."""

    temp: float = 50.0  # 0 = frozen, 50 = ideal, 100 = boiling over.
    swirl_rpm: float = 1.0  # Rotation speed; MAX_RPM or above is a loss.
    # -1 (counter-clockwise) .. 1 (clockwise). Starts at BASELINE_DIR, not 0:
    # at exactly 0 the chase band wouldn't move at all (phase advances by
    # swirl_rpm * swirl_dir), so the ring would look static and safe even
    # though temp/rpm are already drifting -- a real point of confusion in
    # an early playtest ("it was already green and not swirling").
    swirl_dir: float = BASELINE_DIR
    phase: float = 0.0  # 0-1, the bright chase band's position around the ring.
    survival_time: float = 0.0


def update(state: PotionState, dt: float, held: frozenset[str]) -> PotionState:
    """Advance the simulation by ``dt`` seconds given the held controls.

    ``held`` is any of "heat_up", "cool_down", "stir_left", "stir_right".
    Pure function -- no hardware touched, so it's directly testable.
    """
    temp = state.temp + DRIFT_PER_SEC * dt
    if "heat_up" in held:
        temp += HEAT_PER_SEC * dt
    if "cool_down" in held:
        temp -= COOL_PER_SEC * dt

    imbalance = abs(temp - 50.0)
    rpm = state.swirl_rpm + IMBALANCE_TO_RPM * imbalance * dt

    direction = state.swirl_dir
    if "stir_left" in held:
        rpm = max(MIN_RPM, rpm - BRAKE_PER_SEC * dt)
        direction -= STEER_PER_SEC * dt
    if "stir_right" in held:
        rpm = max(MIN_RPM, rpm - BRAKE_PER_SEC * dt)
        direction += STEER_PER_SEC * dt
    if ("stir_left" not in held) and ("stir_right" not in held):
        # Relax toward a nonzero baseline, not a dead stop: the swirl's
        # *speed* (rpm) is the real danger signal, and it never pauses just
        # because the mortal isn't touching the talons, so the swirl's
        # visible rotation shouldn't either -- see render()'s docstring.
        direction += (BASELINE_DIR - direction) * min(1.0, DIR_RELAX_PER_SEC * dt)
    direction = max(-1.0, min(1.0, direction))

    phase = (state.phase + rpm * direction * dt) % 1.0

    return PotionState(
        temp=temp,
        swirl_rpm=rpm,
        swirl_dir=direction,
        phase=phase,
        survival_time=state.survival_time + dt,
    )


def status(state: PotionState) -> Status:
    """Classify a state as still running, a loss, or a win."""
    if (state.temp <= 0.0) or (state.temp >= 100.0) or (state.swirl_rpm >= MAX_RPM):
        return "failure"
    if state.survival_time >= TARGET_SURVIVAL_SEC:
        return "victory"
    return "running"


def _temp_hue(temp: float) -> float:
    """Map temp (0-100) to a hue fraction: blue (cold) -> green (ideal) -> red (hot)."""
    if temp <= 50.0:
        return 0.66 + (0.33 - 0.66) * (temp / 50.0)
    return 0.33 + (0.0 - 0.33) * ((temp - 50.0) / 50.0)


# A fixed speckle texture -- small per-slot hue/brightness jitter, sampled
# once at import time rather than freshly each frame (fresh-per-frame would
# just look like static, not a swirl). render() rotates this fixed pattern
# by ``phase`` instead of regenerating it, and indexes it modulo its own
# length so it still works for any ring size. See render()'s docstring.
_SPECKLE_COUNT = 256
_rng = random.Random(0)
_HUE_JITTER = [(_rng.random() - 0.5) * 0.08 for _ in range(_SPECKLE_COUNT)]
_VALUE_JITTER = [_rng.uniform(0.35, 1.0) for _ in range(_SPECKLE_COUNT)]


def render(state: PotionState, count: int | None = None) -> list[light_control.RGB]:
    """Pure function: the ring's pixel colors for this state.

    Every pixel is colored around the mean hue (from temp), each with a
    small, fixed random jitter in hue and brightness -- like a potion full
    of swirling flecks -- rather than a single flat tone. Rotating that
    fixed speckle pattern by ``phase`` is what reads as the swirl itself:
    position conveys direction, and the fixed jitter travels with it.
    """
    if count is None:
        count = light_control.STRIP_CONFIG.count
    hue = _temp_hue(state.temp)
    offset = round(state.phase * count)

    colors: list[light_control.RGB] = []
    for i in range(count):
        j = (i - offset) % _SPECKLE_COUNT
        pixel_hue = (hue + _HUE_JITTER[j]) % 1.0
        colors.append(_hsv_to_rgb(pixel_hue, 1.0, _VALUE_JITTER[j]))
    return colors


def _hsv_to_rgb(hue: float, saturation: float, value: float) -> light_control.RGB:
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return light_control.RGB(round(r * 255), round(g * 255), round(b * 255))


def _read_held() -> frozenset[str]:
    """Translate the four control buttons' live state into control names."""
    held: set[str] = set()
    if controller.is_held(_HEAT_UP_BUTTON):
        held.add("heat_up")
    if controller.is_held(_COOL_DOWN_BUTTON):
        held.add("cool_down")
    if controller.is_held(_STIR_LEFT_BUTTON):
        held.add("stir_left")
    if controller.is_held(_STIR_RIGHT_BUTTON):
        held.add("stir_right")
    return frozenset(held)


def run(*, select_button: int = 8, start_button: int = 9) -> Outcome:
    """Play one round, blocking until it's won, lost, or aborted.

    Owns its own ambience (starts/stops the bubbling loop) and checks the
    Select+Start combo itself each tick, so the mortal can bail back out to
    mode selection early. cauldron_controller.py's _run_mode_selection()
    calls this from the controller-watching thread, where the usual
    _mode_switch_abort Event isn't a useful abort signal here -- it's
    already set, from the very combo press that opened mode selection in
    the first place.
    """
    audio.start_bubbling()
    state = PotionState()
    try:
        while True:
            controller.poll_pressed()  # Drain the queue; is_held() needs it pumped.
            if controller.is_held(select_button) and controller.is_held(start_button):
                return "aborted"

            state = update(state, TICK_SEC, _read_held())
            light_control.set_pixels(render(state))
            audio.set_ambience_intensity(min(1.0, state.swirl_rpm / MAX_RPM))

            outcome = status(state)
            if outcome != "running":
                print(
                    f"Brew {outcome} at t={state.survival_time:.1f}s: "
                    f"temp={state.temp:.1f} rpm={state.swirl_rpm:.2f}"
                )
                return outcome
            time.sleep(TICK_SEC)
    finally:
        audio.stop_bubbling()
        light_control.leds_off()


def main() -> None:
    global TARGET_SURVIVAL_SEC

    parser = argparse.ArgumentParser(
        description="Play one round of the brew-balancing mini-game."
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=TARGET_SURVIVAL_SEC,
        help=f"Seconds to survive to win (default: {TARGET_SURVIVAL_SEC:g}).",
    )
    args = parser.parse_args()
    TARGET_SURVIVAL_SEC = args.duration

    light_control.setup()
    audio.setup()
    if not controller.setup():
        raise SystemExit("No gamepad connected.")

    print(
        f"Heat up: button_1 (red). Cool down: button_0 (blue). Stir: "
        f"button_4/button_5 (talons). Survive {TARGET_SURVIVAL_SEC:g}s to win. "
        f"Select+Start to quit early."
    )
    outcome = run()
    print(f"Outcome: {outcome}")


if __name__ == "__main__":
    main()
