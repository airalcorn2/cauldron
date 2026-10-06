"""Interactive Halloween cauldron controller.

Six modes, selected with ``--mode``:

  react (default)
    IR beam broken -> bubbling SFX and LED strobe -> a pause for the beam to
    go quiet, so a second item dropped in right after still makes the shot ->
    camera capture (after a warmup so auto-exposure settles on the lit scene)
    -> stage tips to dump the items -> vision model writes a three-witch
    spell about the tray -> concurrent TTS -> sequential playback with the
    ring set to each witch's color.

  story
    Identical pipeline to react, but the vision model is asked for a short
    spooky story featuring the tray's items instead of a rhyming spell, told
    in three parts (one per witch) that continue one another. See
    process_spooky_spell()'s ``generate`` parameter -- the two modes share
    every stage except what they ask the model for.

  joke
    Identical pipeline to react, but the vision model is asked for a fun,
    spooky joke about the tray's items instead of a rhyming spell, again
    split three ways (one per witch) into one joke rather than three.

  prophecy
    Identical pipeline to react, but the vision model is asked for a campy,
    over-dramatic fortune about the mortal's future, reading the tray's
    items as omens, again split three ways (one per witch) into one
    prophecy rather than three.

  request
    IR beam broken -> the witches announce a recipe of objects to fetch ->
    the mortal drops items in (holding the beam instead replays the request,
    recognized throughout the wait -- see _evaluate_attempt) -> photo, once
    the beam goes quiet, then the stage tips to dump the items -> the model
    checks the tray, one attempt only -> a triumphant spell and a green
    shimmer on success, or a comedic curse and a red fizzle on anything
    less.

  category
    Like request, but the witches challenge a property instead of naming
    objects (e.g. "something round") -- the mortal picks what counts, and
    the model judges whatever lands against the property rather than
    matching a fixed list. Same wait/repeat-gesture/one-attempt mechanics
    as request; see process_category_challenge() and
    _evaluate_category_attempt().

The bubbling SFX and LED strobe cover every wait on a slow API call, and
perform_spell() stops them immediately before the witches recite.

Assumed hardware:
  - IR break-beam sensor on GPIO 17               (ir_sensor.py)
  - USB camera at index 0                         (camera.py)
  - WS2812B LED ring on GPIO 10 (SPI0 MOSI)       (light_control.py)
  - Speaker via an I2S amp or USB audio out       (audio.py)
  - Linear actuator below the stage, on GPIO 16 / GPIO 26 (actuator.py)

Each component module also runs on its own for bench testing; see the docstring
at the top of ir_sensor.py, camera.py, light_control.py, audio.py,
spell_generator.py, voice_generator.py, and actuator.py.

    .venv/bin/python cauldron_controller.py                  # React mode, looping.
    .venv/bin/python cauldron_controller.py --mode request   # Request mode, looping.
    .venv/bin/python cauldron_controller.py --mode category  # Category mode, looping.
    .venv/bin/python cauldron_controller.py --mode story     # Story mode, looping.
    .venv/bin/python cauldron_controller.py --mode joke      # Joke mode, looping.
    .venv/bin/python cauldron_controller.py --mode prophecy  # Prophecy mode, looping.
    .venv/bin/python cauldron_controller.py --once           # One round, then exit (smoke test).
    .venv/bin/python cauldron_controller.py --once --paid-voices  # ...with the real premium voices.

The controller runs entirely as your normal user: the LED ring is driven over
SPI (no root), the IR sensor's GPIO goes through the `gpio` group, audio uses
your own session, and the API keys come from your shell. SPI must be enabled
and the core clock pinned first; see docs/step-8-led-ring.md.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import random
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import RPi.GPIO as GPIO

import actuator
import audio
import brew_game
import controller
import ir_sensor
import light_control
import potion_play
import spell_generator
from camera import capture_frame
from voice_generator import (
    FREE_VOICES,
    VOICES,
    VoiceProfile,
    generate_all_speech,
    generate_to_path,
)
from witches import WITCHES, Witch

DEBOUNCE_SEC = 3.0

# Shared: once triggered, how long the beam must stay clear before acting on
# the tray, so a second item dropped in right after the first one still makes
# it into the shot. React mode calls wait_for_quiet() right after its
# triggering break; request mode calls wait_for_drops() instead (see
# _evaluate_attempt), since there the mortal hasn't dropped anything yet when
# the wait starts, and it also needs to recognize the hold-to-repeat gesture.
SETTLE_GAP_SEC = 2.0

# Request-mode tuning.
INGREDIENT_COUNT = 2
HOLD_TO_REPEAT_SEC = 1.2  # Obstruct the beam this long to replay the request.

# Live mode switching via a USB gamepad (see controller.py). The mortal holds
# Select+Start together to open mode selection; a random witch then asks
# which mode to pick, naming these buttons in-universe (see
# MODE_SELECT_OPTIONS below), and the mortal answers by pressing one of them.
# button_0-3 are this pad's four face buttons, each a distinct physical
# color (see BUTTON_COLORS); button_4/button_5 are the plain gray shoulder
# buttons. "up"/"down" (the D-pad) are the two unused inputs left, so
# they're "brew"'s and "play"'s buttons -- those modes are one-shot
# mini-games rather than persistent modes (see _run_mode_selection()), so
# they don't need one of the beam-triggered modes' buttons. Remap here if
# your pad differs.
MODE_BUTTONS: dict[str, str] = {
    "button_0": "react",
    "button_1": "story",
    "button_2": "joke",
    "button_3": "prophecy",
    "button_4": "request",
    "button_5": "category",
    "up": "brew",
    "down": "play",
}

# The ring shows one of these colors while listing/confirming each mode, so
# the mortal sees a direct match to the button they're looking at. button_0-3
# are this pad's actual face-button colors -- confirmed by running
# `.venv/bin/python controller.py` and pressing each one. button_4/button_5
# (the talons) and "up"/"down" (the D-pad, physically unlit) are just
# assigned colors here, distinct from the face buttons' and each other's.
BUTTON_COLORS: dict[str, light_control.RGB] = {
    "button_0": light_control.RGB(30, 90, 255),  # Blue
    "button_1": light_control.RGB(220, 20, 20),  # Red
    "button_2": light_control.RGB(230, 200, 0),  # Yellow
    "button_3": light_control.RGB(60, 200, 40),  # Green
    "button_4": light_control.RGB(0, 220, 220),  # Cyan
    "button_5": light_control.RGB(255, 130, 0),  # Orange
    "up": light_control.RGB(200, 0, 160),  # Magenta
    "down": light_control.RGB(255, 255, 255),  # White
}

# Mode -> ring color, derived from BUTTON_COLORS. Shown steady while that
# mode's button is being named during the listing, and again during the
# confirmation if that mode ends up chosen.
MODE_COLORS: dict[str, light_control.RGB] = {
    mode: BUTTON_COLORS[button] for button, mode in MODE_BUTTONS.items()
}

# Select and Start, held together, open mode selection. Confirmed on this pad
# via `.venv/bin/python controller.py`; remap here if yours differs.
_SELECT_BUTTON = 8
_START_BUTTON = 9

# The hand-written mode-select dialogue itself lives on each witch's profile
# in witches.py (WitchProfile.mode_select_intro/options/confirmations), next
# to her personality and voice settings rather than duplicated here -- these
# are just the views this module actually uses.
MODE_SELECT_INTROS: dict[Witch, str] = {
    w: p.mode_select_intro for w, p in WITCHES.items()
}
MODE_SELECT_OPTIONS: dict[Witch, dict[str, str]] = {
    w: p.mode_select_options for w, p in WITCHES.items()
}
MODE_SELECT_CONFIRMATIONS: dict[Witch, dict[str, str]] = {
    w: p.mode_select_confirmations for w, p in WITCHES.items()
}

MODE_SELECT_DIR = Path("generated/mode_select")

_dump_thread: threading.Thread | None = None

# Set by main() to the --mode default, then only ever changed by
# _run_mode_selection() below. A plain module global is enough: CPython's GIL
# makes a single assignment atomic, and there is exactly one writer.
_pending_mode: str = "react"

# Set the instant mode selection opens; cleared by main() right before
# starting the next round. Threaded into ir_sensor's wait_for_quiet()/
# wait_for_drops() calls below so an in-progress round's indefinite waits --
# the only truly unbounded part of a round -- notice and bail out, rather
# than the switch silently queuing up for whenever the current round would
# have ended on its own. A short TTS line already in flight is allowed to
# finish rather than being killed mid-sentence; the next checkpoint after it
# will catch the abort within a second or two.
_mode_switch_abort = threading.Event()


def _start_ambience() -> None:
    """Start the bubbling SFX and LED strobe."""
    audio.start_bubbling()
    light_control.start_flicker()


def _stop_ambience() -> None:
    """Stop the bubbling SFX and LED strobe. Safe to call any time."""
    light_control.stop_flicker()
    audio.stop_bubbling()


def _mode_select_intro_path(witch: Witch) -> Path:
    return MODE_SELECT_DIR / f"{witch}_intro.mp3"


def _mode_select_option_path(witch: Witch, mode: str) -> Path:
    return MODE_SELECT_DIR / f"{witch}_option_{mode}.mp3"


def _mode_select_confirm_path(witch: Witch, mode: str) -> Path:
    return MODE_SELECT_DIR / f"{witch}_{mode}.mp3"


def _brew_outcome_path(witch: Witch, outcome: str) -> Path:
    return MODE_SELECT_DIR / f"{witch}_brew_{outcome}.mp3"


def _ensure_mode_select_cache() -> None:
    """Synthesize any missing mode-select/brew-outcome voice lines, so both
    are instant.

    Called once from main(), before the trigger loop starts, if a gamepad is
    connected. The 57 lines (3 intros + 24 options + 24 confirmations + 6
    brew outcomes -- "play" has no outcome lines, there's nothing to win or
    lose) essentially never change once written, so a normal run just
    confirms the cache is complete and does nothing.
    """
    missing: list[tuple[str, VoiceProfile, Path]] = []
    for witch in Witch:
        intro_path = _mode_select_intro_path(witch)
        if not intro_path.exists():
            missing.append((MODE_SELECT_INTROS[witch], VOICES[witch], intro_path))
        for mode, text in MODE_SELECT_OPTIONS[witch].items():
            option_path = _mode_select_option_path(witch, mode)
            if not option_path.exists():
                missing.append((text, VOICES[witch], option_path))
        for mode, text in MODE_SELECT_CONFIRMATIONS[witch].items():
            confirm_path = _mode_select_confirm_path(witch, mode)
            if not confirm_path.exists():
                missing.append((text, VOICES[witch], confirm_path))
        for outcome, text in (
            ("victory", WITCHES[witch].brew_victory),
            ("failure", WITCHES[witch].brew_failure),
        ):
            outcome_path = _brew_outcome_path(witch, outcome)
            if not outcome_path.exists():
                missing.append((text, VOICES[witch], outcome_path))

    if len(missing) == 0:
        return
    print(f"Synthesizing {len(missing)} mode-select voice line(s).")

    async def _generate_all() -> None:
        # Sequential, not gathered: ElevenLabs' concurrent-request limit
        # (even on paid tiers) rejects many requests fired at once with a
        # 429. This only runs once, to fill a cold cache, so there's no
        # latency pressure to parallelize it.
        for text, profile, path in missing:
            await generate_to_path(text, profile, path)

    asyncio.run(_generate_all())
    print("Mode-select cache ready.")


def _wait_for_mode_selection(witch: Witch) -> str:
    """Block until the mortal presses one of the six mode buttons.

    Meanwhile, left/right on the D-pad (not itself one of the mode
    buttons -- see MODE_BUTTONS) lets the mortal browse back through the
    list: each press moves the cursor one mode over and replays that
    mode's option line, in case they missed it or forgot which button went
    with which mode. A further left/right press, or a mode button, cuts a
    still-playing repeat short rather than waiting for it to finish -- same
    as during the initial listing (see audio.play_file's ``should_stop``).
    The ring shows the rainbow sweep (see light_control.start_rainbow())
    whenever genuinely idle -- waiting for the first press, or again after
    a repeat finishes with nothing queued up next -- and a mode's fixed
    color only while that mode's line is actually playing.

    No timeout, same as the rest of the prop's waits (see
    ir_sensor.wait_for_drops()) -- the mortal takes as long as they like.
    """
    modes = list(MODE_BUTTONS.values())
    index = 0
    selected: str | None = None
    nav: str | None = None

    def check_input() -> bool:
        nonlocal selected, nav
        pressed = controller.poll_pressed()
        if pressed is not None:
            mode = MODE_BUTTONS.get(pressed)
            if mode is not None:
                selected = mode
            elif pressed in ("left", "right"):
                nav = pressed
        return (selected is not None) or (nav is not None)

    light_control.start_rainbow()
    while True:
        if selected is not None:
            light_control.stop_rainbow()
            return selected
        if nav is None:
            if not check_input():
                time.sleep(0.02)
                continue
            if selected is not None:
                light_control.stop_rainbow()
                return selected

        light_control.stop_rainbow()
        index = (index + (1 if nav == "right" else -1)) % len(modes)
        nav = None
        light_control.set_leds(MODE_COLORS[modes[index]])
        audio.play_file(
            _mode_select_option_path(witch, modes[index]),
            volume=audio.WITCH_VOLUMES[witch],
            should_stop=check_input,
        )
        if (selected is None) and (nav is None):
            light_control.start_rainbow()


def _run_mode_selection() -> None:
    """Select+Start detected: ask a random witch which mode to pick, wait for
    the mortal's answer, then have that same witch confirm it with the ring
    held in the new mode's color for the duration of the confirmation line.

    The idle rainbow (see main()) is already running when the combo is
    pressed, so the intro just continues it uninterrupted; it switches to
    each mode's own fixed color as that mode's button is named (one option
    line per mode -- see MODE_SELECT_OPTIONS), pausing on it for exactly
    that line's duration before moving to the next. Holds steady on just
    the chosen one during the confirmation, then resumes the idle rainbow
    once selection is done. The mortal doesn't have to wait out the intro
    or listing, though -- pressing a mode button at any point cuts the
    current line short (see audio.play_file's ``should_stop``) and jumps
    straight to that mode's confirmation. Runs on the controller-watching
    thread and blocks it for as long as selection takes -- fine, since
    watching for the combo is all that thread does otherwise. Sets
    _mode_switch_abort first, so an in-progress round's indefinite waits
    notice and bail out.
    """
    global _pending_mode
    print("Controller: mode-select combo pressed.")
    _mode_switch_abort.set()
    _stop_ambience()
    previous_mode = _pending_mode

    witch = random.choice(list(Witch))
    selected: str | None = None

    def check_early_press() -> bool:
        nonlocal selected
        pressed = controller.poll_pressed()
        if pressed is not None:
            mode = MODE_BUTTONS.get(pressed)
            if mode is not None:
                selected = mode
        return selected is not None

    light_control.start_rainbow()
    audio.play_file(
        _mode_select_intro_path(witch),
        volume=audio.WITCH_VOLUMES[witch],
        should_stop=check_early_press,
    )
    light_control.stop_rainbow()

    if selected is None:
        for mode in MODE_BUTTONS.values():
            light_control.set_leds(MODE_COLORS[mode])
            audio.play_file(
                _mode_select_option_path(witch, mode),
                volume=audio.WITCH_VOLUMES[witch],
                should_stop=check_early_press,
            )
            if selected is not None:
                break
        light_control.leds_off()

    if selected is None:
        selected = _wait_for_mode_selection(witch)
    _pending_mode = selected

    light_control.set_leds(MODE_COLORS[selected])
    audio.play_file(
        _mode_select_confirm_path(witch, selected), volume=audio.WITCH_VOLUMES[witch]
    )
    light_control.leds_off()

    if selected in ("brew", "play"):
        # One-shot mini-games/activities, not persistent modes: run it right
        # now and leave _pending_mode as it was, so the beam-triggered loop
        # in main() resumes whatever mode was active before this detour.
        if selected == "brew":
            process_brew_game()
        else:
            process_potion_play()
        _pending_mode = previous_mode
        light_control.start_rainbow()  # Back to idle.
        return

    light_control.start_rainbow()  # Back to idle.
    print(f"Controller: switched to {selected} mode.")


def _watch_controller() -> None:
    """Background thread: watch for the Select+Start combo and open mode
    selection the instant both are held together.

    Runs for the life of the process (daemon thread, started once from
    main() if a gamepad is connected). Edge-triggers on the transition into
    "both held" so holding the combo doesn't repeat-fire selection.
    """
    combo_was_held = False
    while True:
        controller.poll_pressed()  # Drain the event queue; is_held() needs it pumped.
        is_held = controller.is_held(_SELECT_BUTTON) and controller.is_held(
            _START_BUTTON
        )
        if is_held and (not combo_was_held):
            _run_mode_selection()
        combo_was_held = is_held
        time.sleep(0.02)


def start_stage_dump() -> None:
    """Dump the stage on a background thread so the show can keep going.

    No-op if a dump is already in flight.
    """
    global _dump_thread
    if (_dump_thread is not None) and _dump_thread.is_alive():
        return
    _dump_thread = threading.Thread(target=actuator.dump_stage, daemon=True)
    _dump_thread.start()


def wait_for_stage_dump() -> None:
    """Block until any in-flight dump finishes. Call before the next photo."""
    if _dump_thread is not None:
        _dump_thread.join()


def recite_spell(spell_paths: Mapping[Witch, Path]) -> None:
    """Play each witch's line in turn with the ring set to that witch's color.

    Recites in the mapping's own order, which carries the spell's speaking
    order through from the prompt that asked for it.
    """
    for witch, path in spell_paths.items():
        light_control.set_leds(light_control.WITCH_COLORS[witch])
        audio.play_file(path, volume=audio.WITCH_VOLUMES[witch])
    light_control.leds_off()


def perform_spell(
    spell: spell_generator.Spell,
    *,
    strict: bool,
    paid_voices: bool = False,
    label: str = "Generating witch voices.",
) -> dict[Witch, Path] | None:
    """Synthesize a spell's three lines and recite them.

    Keeps the "cauldron working" ambience (bubbling SFX + LED strobe) running
    through voice generation so there is no silent gap, then stops it and
    recites. Returns the generated ``{witch: path}`` mapping -- reusable to
    replay the same lines later without regenerating them, e.g., a repeat
    gesture -- or None if TTS failed and nothing was recited (never in strict
    mode, where the failure is raised instead).

    The live show (``strict=False``) always uses the premium voices. The
    ``--once`` smoke test defaults to the free voices to avoid spending
    premium quota on a test run; pass ``paid_voices=True`` to hear the real
    voices during that test instead.
    """
    print(label)
    _start_ambience()
    voices: Mapping[Witch, VoiceProfile] = (
        VOICES if (paid_voices or (not strict)) else FREE_VOICES
    )
    try:
        spell_paths = asyncio.run(
            generate_all_speech(dict(spell), voices, sequential=strict)
        )
    except Exception as exc:
        if strict:
            raise
        print(f"TTS error: {exc}")
        _stop_ambience()
        return None

    _stop_ambience()
    recite_spell(spell_paths)
    return spell_paths


def _voice_reaction(
    make_spell: Callable[[], spell_generator.Spell],
    *,
    strict: bool,
    paid_voices: bool = False,
    label: str,
) -> None:
    """Generate a reaction spell and recite it, skipping it if the API fails.

    The generation call has to happen inside the try, so it is passed in unrun:
    evaluating it in the argument list would let the exception escape.
    """
    try:
        spell = make_spell()
    except Exception as exc:
        if strict:
            raise
        print(f"Spell API error: {exc}")
        # The caller lights a cue next, which would fight the strobe thread.
        _stop_ambience()
        return
    perform_spell(spell, strict=strict, paid_voices=paid_voices, label=label)


def process_spooky_spell(
    strict: bool = False,
    *,
    paid_voices: bool = False,
    generate: Callable[
        [Path], spell_generator.Spell
    ] = spell_generator.generate_spell_from_image,
) -> None:
    """React/story mode: one full trigger cycle.

    Both modes share an identical pipeline -- capture, ask the vision model
    for something to recite, then recite it -- and only differ in what they
    ask for, so ``generate`` is the one swappable piece: react mode's default
    asks for a rhyming spell, story mode passes
    spell_generator.generate_story_from_image for a short story instead.

    A failed stage normally just ends the round, leaving the prop up for the
    next trigger. With ``strict=True`` (the ``--once`` smoke test) failures are
    raised instead, so a broken camera, API, or speaker is reported loudly.
    """
    print("Object detected. Starting the cauldron sequence.")
    _start_ambience()

    try:
        ir_sensor.wait_for_quiet(SETTLE_GAP_SEC, abort=_mode_switch_abort)
        if _mode_switch_abort.is_set():
            print("Round interrupted by mode switch.\n")
            return
        wait_for_stage_dump()
        img_path = capture_frame()
        if img_path is None:
            if strict:
                raise RuntimeError("camera capture failed")
            print("Camera capture failed. Ending the round.")
            return
        print(f"Captured {img_path}.")
        start_stage_dump()

        print("Consulting the spirits (vision model).")
        try:
            spell = generate(img_path)
        except Exception as exc:
            if strict:
                raise
            print(f"API error or timeout: {exc}. Ending the round.")
            return
        print(f"Generated: {dict(spell)}")

        perform_spell(spell, strict=strict, paid_voices=paid_voices)
        print("Recitation complete.\n")

    finally:
        # Guarantee the ambience never gets stuck running.
        _stop_ambience()


def _wait_for_drop_then_capture(
    spell_paths: Mapping[Witch, Path], *, strict: bool
) -> Path | None:
    """Wait for the mortal to drop something in, then photograph the tray.

    The announcement was just spoken, so the prop is silent; ambience only
    starts once a beam break is confirmed to be a drop rather than a
    hold-to-repeat gesture (on_drop), covering the capture/evaluate work
    that follows. A repeat (on_repeat) just replays ``spell_paths`` (the
    announcement's own TTS output, handed in by the caller) and never
    starts ambience itself -- the next real drop will, same as always, if
    one follows. Returns None if the camera failed.

    Leaves the ambience running: the caller's next perform_spell() stops it for
    the recitation, so there is no silent gap while the outcome voices render.
    Also returns None (silently -- no "camera failed" message) if a live mode
    switch interrupted the wait; the caller tells the two apart by checking
    _mode_switch_abort itself.
    """

    def on_repeat() -> None:
        recite_spell(spell_paths)

    ir_sensor.wait_for_drops(
        SETTLE_GAP_SEC,
        hold=HOLD_TO_REPEAT_SEC,
        on_obstruct=_stop_ambience,
        on_drop=_start_ambience,
        on_repeat=on_repeat,
        abort=_mode_switch_abort,
    )
    if _mode_switch_abort.is_set():
        return None
    wait_for_stage_dump()
    img_path = capture_frame()
    if img_path is None:
        if strict:
            raise RuntimeError("camera capture failed")
        print("Camera capture failed.")
        return None
    print(f"Captured {img_path}.")
    start_stage_dump()
    return img_path


def _evaluate_attempt(
    recipe: spell_generator.Recipe,
    spell_paths: Mapping[Witch, Path],
    *,
    strict: bool,
) -> spell_generator.RoundResult | None:
    """Wait for the mortal to drop items, then score the tray against ``recipe``.

    Returns None if the camera or model failed.
    """
    img_path = _wait_for_drop_then_capture(spell_paths, strict=strict)
    if img_path is None:
        return None
    try:
        return spell_generator.evaluate_tray(recipe, img_path)
    except Exception as exc:
        if strict:
            raise
        print(f"Evaluation API error: {exc}")
        return None


def _evaluate_category_attempt(
    challenge: spell_generator.Challenge,
    spell_paths: Mapping[Witch, Path],
    *,
    strict: bool,
) -> spell_generator.CategoryOutcome | None:
    """Wait for the mortal to drop something in, then judge it against ``challenge``.

    Returns None if the camera or model failed.
    """
    img_path = _wait_for_drop_then_capture(spell_paths, strict=strict)
    if img_path is None:
        return None
    try:
        return spell_generator.evaluate_category(challenge, img_path)
    except Exception as exc:
        if strict:
            raise
        print(f"Evaluation API error: {exc}")
        return None


def process_requested_spell(
    *,
    strict: bool,
    ingredient_count: int,
    paid_voices: bool = False,
) -> None:
    """Request mode: the witches name a recipe and grade what the mortal brings.

    One attempt only: whatever is on the tray once the beam settles is what
    gets graded, win or lose.
    """
    print("Beam broken. The witches will name their price.")

    # Ambience runs from here until perform_spell() stops it for the recitation;
    # the finally is only a safety net for unexpected errors.
    _start_ambience()
    try:
        try:
            recipe = spell_generator.request_recipe(ingredient_count)
        except Exception as exc:
            if strict:
                raise
            print(f"Recipe API error: {exc}")
            recipe = spell_generator.random_fallback_recipe()
        print(f"Recipe: {recipe.summary()}")

        spell_paths = perform_spell(
            recipe.announce(),
            strict=strict,
            paid_voices=paid_voices,
            label="Voicing the request.",
        )
        if spell_paths is None:
            return  # TTS fell back; end the round.

        result = _evaluate_attempt(recipe, spell_paths, strict=strict)
        if result is None:  # Camera/model failed, or a mode switch interrupted it.
            if _mode_switch_abort.is_set():
                print("Round interrupted by mode switch.\n")
                return
            _stop_ambience()  # Clear the ring for the cue.
            light_control.fizzle()
            print("Round complete: evaluation failed.\n")
            return

        print(
            f"Outcome: {result.outcome.name} "
            f"(found {sorted(i.answer for i in result.found)}, "
            f"missing {sorted(i.answer for i in result.missing)})"
        )

        if result.outcome is spell_generator.Outcome.SUCCESS:
            _voice_reaction(
                functools.partial(spell_generator.generate_outcome_spell, result),
                strict=strict,
                paid_voices=paid_voices,
                label="Voicing the triumph.",
            )
            light_control.celebrate()
            print("Round complete: success.\n")
            return

        _voice_reaction(
            functools.partial(spell_generator.generate_outcome_spell, result),
            strict=strict,
            paid_voices=paid_voices,
            label="Voicing the curse.",
        )
        light_control.fizzle()
        print("Round complete: failure.\n")
    finally:
        _stop_ambience()


def process_category_challenge(*, strict: bool, paid_voices: bool = False) -> None:
    """Category mode: the witches challenge the mortal to bring something
    matching a property, then judge whatever lands against it.

    One attempt only, same as request mode.
    """
    print("Beam broken. The witches pose a challenge.")

    _start_ambience()
    try:
        try:
            challenge = spell_generator.category_challenge()
        except Exception as exc:
            if strict:
                raise
            print(f"Challenge API error: {exc}")
            challenge = spell_generator.random_fallback_challenge()
        print(f"Challenge: something {challenge.category}")

        spell_paths = perform_spell(
            challenge.announce(),
            strict=strict,
            paid_voices=paid_voices,
            label="Voicing the challenge.",
        )
        if spell_paths is None:
            return  # TTS fell back; end the round.

        result = _evaluate_category_attempt(challenge, spell_paths, strict=strict)
        if result is None:  # Camera/model failed, or a mode switch interrupted it.
            if _mode_switch_abort.is_set():
                print("Round interrupted by mode switch.\n")
                return
            _stop_ambience()  # Clear the ring for the cue.
            light_control.fizzle()
            print("Round complete: evaluation failed.\n")
            return

        print(f"Satisfied: {result.satisfied} (saw {list(result.items_seen)})")

        if result.satisfied:
            _voice_reaction(
                functools.partial(
                    spell_generator.generate_category_outcome_spell, result
                ),
                strict=strict,
                paid_voices=paid_voices,
                label="Voicing the triumph.",
            )
            light_control.celebrate()
            print("Round complete: success.\n")
            return

        _voice_reaction(
            functools.partial(spell_generator.generate_category_outcome_spell, result),
            strict=strict,
            paid_voices=paid_voices,
            label="Voicing the curse.",
        )
        light_control.fizzle()
        print("Round complete: failure.\n")
    finally:
        _stop_ambience()


def process_brew_game() -> None:
    """The "brew" mini-game: balance heat and swirl using the gamepad alone.

    No camera, no actuator -- nothing is dropped in, so there's nothing to
    photograph or dump. See brew_game.run() for the game loop itself; this
    just picks a witch to announce the outcome and lights the matching cue.
    """
    print("Brewing game: balance the heat and the swirl.")
    witch = random.choice(list(Witch))
    outcome = brew_game.run(select_button=_SELECT_BUTTON, start_button=_START_BUTTON)
    if outcome == "aborted":
        print("Brewing game interrupted by mode switch.\n")
        return

    audio.play_file(
        _brew_outcome_path(witch, outcome), volume=audio.WITCH_VOLUMES[witch]
    )
    if outcome == "victory":
        light_control.celebrate()
        print("Brewing game complete: victory.\n")
    else:
        light_control.fizzle()
        print("Brewing game complete: failure.\n")


def process_potion_play() -> None:
    """The "play" mini-mode: no game, just manipulate the lights for fun.

    No win, no lose, no outcome line -- see potion_play.run(), which blocks
    until the mortal presses Select+Start again to leave.
    """
    print("Potion play: no pressure, just lights.")
    potion_play.run(select_button=_SELECT_BUTTON, start_button=_START_BUTTON)
    print("Potion play complete.\n")


def main(
    *,
    mode: str = "react",
    once: bool = False,
    ingredient_count: int = INGREDIENT_COUNT,
    paid_voices: bool = False,
) -> None:
    """Set up the hardware, then run the trigger loop (or one round for ``once``)."""
    global _pending_mode
    # `systemctl stop` (see systemd/cauldron.service) sends SIGTERM, which
    # Python otherwise ignores by just dying immediately -- skipping the
    # `finally` below and its cleanup (don't cut power to the actuator
    # mid-stroke, release the GPIO pins). Making it raise KeyboardInterrupt,
    # same as Ctrl-C, routes it through the exact same shutdown path.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    ir_sensor.setup()
    light_control.setup()
    audio.setup()
    actuator.setup()
    _pending_mode = mode

    if controller.setup():
        _ensure_mode_select_cache()
        potion_play.ensure_cache()
        threading.Thread(target=_watch_controller, daemon=True).start()
        print("Live mode switching enabled via the USB gamepad.")

    prefix = "Smoke test. " if once else ""
    print(f"{prefix}Cauldron ready in {mode} mode. Break the beam to begin.")

    last_trigger = 0.0
    light_control.start_rainbow()
    try:
        while True:
            if ir_sensor.beam_broken() and (time.time() - last_trigger > DEBOUNCE_SEC):
                last_trigger = time.time()
                light_control.stop_rainbow()
                _mode_switch_abort.clear()
                active_mode = _pending_mode
                if active_mode == "request":
                    process_requested_spell(
                        strict=once,
                        ingredient_count=ingredient_count,
                        paid_voices=paid_voices,
                    )
                elif active_mode == "category":
                    process_category_challenge(strict=once, paid_voices=paid_voices)
                elif active_mode == "story":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_story_from_image,
                    )
                elif active_mode == "joke":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_joke_from_image,
                    )
                elif active_mode == "prophecy":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_prophecy_from_image,
                    )
                else:
                    process_spooky_spell(strict=once, paid_voices=paid_voices)
                if once and (not _mode_switch_abort.is_set()):
                    print("Smoke test PASSED.")
                    return
                light_control.start_rainbow()

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass

    finally:
        wait_for_stage_dump()  # Don't cut power to the motor mid-stroke.
        light_control.stop_rainbow()
        # Unconditional, not just a consequence of stop_rainbow(): if
        # shutdown lands mid mode-select (a daemon thread painting solid
        # colors directly, not through the rainbow thread), that thread is
        # killed with no chance to blank the ring itself, and
        # stop_rainbow() is a no-op since the rainbow was already stopped
        # for the menu -- leaving a stray color lit forever otherwise.
        light_control.leds_off()
        GPIO.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the interactive Halloween cauldron controller."
    )
    parser.add_argument(
        "--mode",
        choices=("react", "request", "category", "story", "joke", "prophecy"),
        default="react",
        help="react: spell about whatever is dropped. request: the witches name "
        "ingredients to fetch. category: the witches challenge a property to "
        "match. story: a short spooky story about whatever is dropped. joke: "
        "a fun, spooky joke about whatever is dropped. prophecy: a campy, "
        "over-dramatic fortune about whatever is dropped (default: react).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one full round (smoke test), raising on any stage failure "
        "instead of logging it and ending the round.",
    )
    parser.add_argument(
        "--paid-voices",
        action="store_true",
        help="With --once, use the premium ElevenLabs voices instead of the "
        "free ones (default: free, to avoid spending quota on a test run). "
        "The live show always uses the premium voices regardless of this flag.",
    )
    parser.add_argument(
        "--ingredients",
        type=int,
        default=INGREDIENT_COUNT,
        help=f"request mode: ingredients per recipe (default: {INGREDIENT_COUNT}).",
    )
    args = parser.parse_args()

    try:
        main(
            mode=args.mode,
            once=args.once,
            ingredient_count=args.ingredients,
            paid_voices=args.paid_voices,
        )
    except Exception as exc:
        if not args.once:
            raise
        print(f"Smoke test FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
