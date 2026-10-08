"""Controller mini-game: keep the brew's heat and swirl in balance.

D-pad up/down push the potion's temperature up/down -- same buttons as
potion_play.py's heat control, held rather than tapped since here the
temperature itself (not a discrete level) is what's being driven. Straying
from the sweet spot speeds up the swirl. The talons (button_4/button_5)
both brake the swirl, and each also steers its direction toward itself.
Boil over, freeze, or spin out of control and the brew is lost; hold
steady long enough and it's a win. The ring renders the state live: speckled
colors sampled around a mean hue (blue below the sweet spot, red above),
desaturating to white right at it, rotate around it at the current swirl
speed and direction.

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

# Physics tuning, found by feel. Either control can end the round on its
# own within a similar ~10-20s neglect window, so both need real attention,
# while attentive play comfortably makes the 30s target.
DRIFT_PER_SEC = 4.0  # Base drift magnitude at t=0; see DRIFT_RAMP_PER_SEC and PotionState.drift_dir.
DRIFT_RAMP_PER_SEC = 0.05  # Drift speeds up over the round -- 2.5x by the 30s target -- so a steady rhythm stops being enough.
HEAT_PER_SEC = 45.0  # heat_up's effect on temp -- crosses the full 0-100 range in a bit over a second, so brief taps (not holds) are what keeps temp near the sweet spot.
COOL_PER_SEC = 45.0  # cool_down's effect on temp.
IMBALANCE_TO_RPM = 0.02  # How much straying from 50 speeds up the swirl.
BRAKE_PER_SEC = 2.5  # Talon braking strength on swirl_rpm.
STEER_PER_SEC = 1.5  # Talon steering strength on swirl_dir.
BASELINE_DIR = 0.6  # Magnitude swirl_dir relaxes toward when neither talon is held.
DIR_RELAX_PER_SEC = 0.4  # How fast swirl_dir relaxes toward its current rest_dir.
DIR_STALL_THRESHOLD = 0.2  # How close to 0 swirl_dir must be, with no talon held, before rest_dir's sign gets re-rolled -- see PotionState.rest_dir.
TEMP_STALL_THRESHOLD = 5.0  # Same idea as DIR_STALL_THRESHOLD, for drift_dir when temp coasts near 50.
MIN_RPM = 0.2
MAX_RPM = 6.0  # Exceeding this is a loss.
TARGET_SURVIVAL_SEC = 30.0  # Default win condition.

# How far the starting state is randomized from its nominal values (50.0
# temp, 1.0 rpm) each round, so rounds don't all begin identically.
INITIAL_TEMP_RANGE = 30.0  # Starting temp: 50 +/- this.
INITIAL_RPM_RANGE = 0.4  # Starting swirl_rpm: 1.0 +/- this.

Status = Literal["running", "victory", "failure"]
Outcome = Literal["victory", "failure", "aborted"]

_STIR_LEFT_BUTTON = 4  # Left talon.
_STIR_RIGHT_BUTTON = 5  # Right talon.


@dataclass(frozen=True, slots=True)
class PotionState:
    """The brew's full state. Immutable -- update() returns a new one."""

    temp: float = 50.0  # 0 = frozen, 50 = ideal, 100 = boiling over.
    # +1 (toward boiling) or -1 (toward frozen): which way temp drifts on
    # its own. Re-rolled by update() whenever temp stalls near 50 with
    # neither heat_up nor cool_down held (see TEMP_STALL_THRESHOLD) rather
    # than fixed at +1, so coasting near the sweet spot doesn't always
    # resume heating up.
    drift_dir: float = 1.0
    swirl_rpm: float = 1.0  # Rotation speed; MAX_RPM or above is a loss.
    # -1 (counter-clockwise) .. 1 (clockwise). Starts at rest_dir, not 0:
    # at exactly 0 the chase band wouldn't move at all (phase advances by
    # swirl_rpm * swirl_dir), so the ring would look static and safe even
    # though temp/rpm are already drifting.
    swirl_dir: float = BASELINE_DIR
    # +-BASELINE_DIR: the sign swirl_dir relaxes toward with no talon held.
    # Re-rolled by update() whenever swirl_dir stalls near 0 (see
    # DIR_STALL_THRESHOLD), so braking the swirl to a stop doesn't always
    # resume the same direction.
    rest_dir: float = BASELINE_DIR
    phase: float = 0.0  # 0-1, the bright chase band's position around the ring.
    survival_time: float = 0.0


def initial_state() -> PotionState:
    """A fresh, randomized starting state for one round.

    temp and swirl_rpm are randomized within INITIAL_TEMP_RANGE/
    INITIAL_RPM_RANGE of their nominal values, and drift_dir/swirl_dir/
    rest_dir's signs are each randomized too, so rounds don't all begin
    identically and the swirl doesn't always start (or resume, after being
    braked to a stop) the same way. swirl_dir's magnitude stays
    BASELINE_DIR either way, never 0 -- see its docstring for why.
    """
    return PotionState(
        temp=50.0 + random.uniform(-INITIAL_TEMP_RANGE, INITIAL_TEMP_RANGE),
        drift_dir=random.choice((-1.0, 1.0)),
        swirl_rpm=max(
            MIN_RPM, 1.0 + random.uniform(-INITIAL_RPM_RANGE, INITIAL_RPM_RANGE)
        ),
        swirl_dir=random.choice((-1.0, 1.0)) * BASELINE_DIR,
        rest_dir=random.choice((-1.0, 1.0)) * BASELINE_DIR,
    )


def update(state: PotionState, dt: float, held: frozenset[str]) -> PotionState:
    """Advance the simulation by ``dt`` seconds given the held controls.

    ``held`` is any of "heat_up", "cool_down", "stir_left", "stir_right".
    Pure function -- no hardware touched, so it's directly testable.
    """
    drift_dir = state.drift_dir
    actively_adjusting = ("heat_up" in held) or ("cool_down" in held)
    if (not actively_adjusting) and (abs(state.temp - 50.0) < TEMP_STALL_THRESHOLD):
        drift_dir = random.choice((-1.0, 1.0))
    drift = DRIFT_PER_SEC * (1.0 + DRIFT_RAMP_PER_SEC * state.survival_time) * drift_dir
    temp = state.temp + drift * dt
    if "heat_up" in held:
        temp += HEAT_PER_SEC * dt
    if "cool_down" in held:
        temp -= COOL_PER_SEC * dt

    imbalance = abs(temp - 50.0)
    rpm = state.swirl_rpm + IMBALANCE_TO_RPM * imbalance * dt

    direction = state.swirl_dir
    rest_dir = state.rest_dir
    if "stir_left" in held:
        rpm = max(MIN_RPM, rpm - BRAKE_PER_SEC * dt)
        direction -= STEER_PER_SEC * dt
    if "stir_right" in held:
        rpm = max(MIN_RPM, rpm - BRAKE_PER_SEC * dt)
        direction += STEER_PER_SEC * dt
    if ("stir_left" not in held) and ("stir_right" not in held):
        # Relax toward a nonzero rest_dir, not a dead stop: the swirl's
        # *speed* (rpm) is the real danger signal, and it never pauses just
        # because the mortal isn't touching the talons, so the swirl's
        # visible rotation shouldn't either -- see render()'s docstring.
        # Stalled near 0 (braked to a stop by the talons, then released)?
        # Re-roll which way it'll resume rather than always picking the
        # fixed +BASELINE_DIR -- see DIR_STALL_THRESHOLD.
        if abs(direction) < DIR_STALL_THRESHOLD:
            rest_dir = random.choice((-1.0, 1.0)) * BASELINE_DIR
        direction += (rest_dir - direction) * min(1.0, DIR_RELAX_PER_SEC * dt)
    direction = max(-1.0, min(1.0, direction))

    phase = (state.phase + rpm * direction * dt) % 1.0

    return PotionState(
        temp=temp,
        drift_dir=drift_dir,
        swirl_rpm=rpm,
        swirl_dir=direction,
        rest_dir=rest_dir,
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
    """Blue below the sweet spot, red above it.

    Paired with _temp_saturation() in render(): which of these two hues
    shows at all, not where it falls on a continuous sweep, is what signals
    which way temp is off. A blue-to-green-to-red sweep (the original
    design) put the "ideal" color in the region of hue space human vision
    discriminates worst, so early drift away from it was hard to notice.
    """
    return 0.66 if temp <= 50.0 else 0.0


def _temp_saturation(temp: float) -> float:
    """0 (white/neutral) at the sweet spot, ramping to 1 at either extreme.

    Any colorfulness at all is the warning now, rather than a particular
    shade of green being "correct" -- a much easier thing to notice at a
    glance, especially through the speckle jitter below.
    """
    return min(1.0, abs(temp - 50.0) / 50.0)


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

    Every pixel is colored around the mean hue and saturation (both from
    temp -- see _temp_hue()/_temp_saturation()), each with a small, fixed
    random jitter in hue and brightness -- like a potion full of swirling
    flecks -- rather than a single flat tone. Rotating that fixed speckle
    pattern by ``phase`` is what reads as the swirl itself: position
    conveys direction, and the fixed jitter travels with it.
    """
    if count is None:
        count = light_control.STRIP_CONFIG.count
    hue = _temp_hue(state.temp)
    saturation = _temp_saturation(state.temp)
    offset = round(state.phase * count)

    colors: list[light_control.RGB] = []
    for i in range(count):
        j = (i - offset) % _SPECKLE_COUNT
        pixel_hue = (hue + _HUE_JITTER[j]) % 1.0
        colors.append(_hsv_to_rgb(pixel_hue, saturation, _VALUE_JITTER[j]))
    return colors


def _hsv_to_rgb(hue: float, saturation: float, value: float) -> light_control.RGB:
    r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
    return light_control.RGB(round(r * 255), round(g * 255), round(b * 255))


def _read_held() -> frozenset[str]:
    """Translate the four controls' live state into control names."""
    held: set[str] = set()
    if controller.is_direction_held("up"):
        held.add("heat_up")
    if controller.is_direction_held("down"):
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
    state = initial_state()
    try:
        while True:
            controller.poll_pressed()  # Drain the queue; is_held() needs it pumped.
            if controller.is_held(select_button) and controller.is_held(start_button):
                light_control.leds_off()  # No outcome line follows an abort.
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
        # Deliberately no leds_off() here on a victory/failure return: the
        # ring should stay exactly as it last rendered through the witch's
        # outcome line, not snap to black before she's even spoken -- see
        # cauldron_controller.process_brew_game(), which decides the
        # follow-up visual (fade_to_black()/victory_swirl()) after that
        # line plays. The "aborted" path blanks it directly above instead,
        # since no outcome line follows an abort.
        audio.stop_bubbling()


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
        f"Heat up: D-pad up. Cool down: D-pad down. Stir: "
        f"button_4/button_5 (talons). Survive {TARGET_SURVIVAL_SEC:g}s to win. "
        f"Select+Start to quit early."
    )
    outcome = run()
    print(f"Outcome: {outcome}")
    # Mirrors cauldron_controller.process_brew_game()'s follow-up (minus the
    # witch's outcome line, which this standalone run has no witch for) so
    # a bench test doesn't just leave the ring lit at whatever it last
    # rendered, and so these effects are directly bench-testable too.
    if outcome == "victory":
        light_control.victory_swirl()
    elif outcome == "failure":
        light_control.fade_to_black()


if __name__ == "__main__":
    main()
