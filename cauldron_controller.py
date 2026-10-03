"""Interactive Halloween cauldron controller.

Five modes, selected with ``--mode``:

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
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import RPi.GPIO as GPIO

import actuator
import audio
import ir_sensor
import light_control
import spell_generator
from camera import capture_frame
from voice_generator import FREE_VOICES, VOICES, VoiceProfile, generate_all_speech
from witches import Witch

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

_dump_thread: threading.Thread | None = None


def _start_ambience() -> None:
    """Start the bubbling SFX and LED strobe."""
    audio.start_bubbling()
    light_control.start_flicker()


def _stop_ambience() -> None:
    """Stop the bubbling SFX and LED strobe. Safe to call any time."""
    light_control.stop_flicker()
    audio.stop_bubbling()


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
        ir_sensor.wait_for_quiet(SETTLE_GAP_SEC)
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


def _evaluate_attempt(
    recipe: spell_generator.Recipe,
    spell_paths: Mapping[Witch, Path],
    *,
    strict: bool,
) -> spell_generator.RoundResult | None:
    """Wait for the mortal to drop items, then photograph the tray and score it.

    The request was just spoken, so the prop is silent; ambience only starts
    once a beam break is confirmed to be an item rather than a hold-to-repeat
    gesture (on_drop), covering the capture/evaluate work that follows. A
    repeat (on_repeat) just replays ``spell_paths`` (the request
    announcement's own TTS output, handed in by the caller) and never starts
    ambience itself -- the next real drop will, same as always, if one
    follows. Returns None if the camera or model failed.

    Leaves the ambience running: the caller's next perform_spell() stops it for
    the recitation, so there is no silent gap while the outcome voices render.
    """

    def on_repeat() -> None:
        recite_spell(spell_paths)

    ir_sensor.wait_for_drops(
        SETTLE_GAP_SEC,
        hold=HOLD_TO_REPEAT_SEC,
        on_obstruct=_stop_ambience,
        on_drop=_start_ambience,
        on_repeat=on_repeat,
    )
    wait_for_stage_dump()
    img_path = capture_frame()
    if img_path is None:
        if strict:
            raise RuntimeError("camera capture failed")
        print("Camera capture failed.")
        return None
    print(f"Captured {img_path}.")
    start_stage_dump()
    try:
        return spell_generator.evaluate_tray(recipe, img_path)
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
        if result is None:  # The camera or the model failed; nothing to grade.
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


def main(
    *,
    mode: str = "react",
    once: bool = False,
    ingredient_count: int = INGREDIENT_COUNT,
    paid_voices: bool = False,
) -> None:
    """Set up the hardware, then run the trigger loop (or one round for ``once``)."""
    ir_sensor.setup()
    light_control.setup()
    audio.setup()
    actuator.setup()

    prefix = "Smoke test. " if once else ""
    print(f"{prefix}Cauldron ready in {mode} mode. Break the beam to begin.")

    last_trigger = 0.0
    try:
        while True:
            if ir_sensor.beam_broken() and (time.time() - last_trigger > DEBOUNCE_SEC):
                last_trigger = time.time()
                if mode == "request":
                    process_requested_spell(
                        strict=once,
                        ingredient_count=ingredient_count,
                        paid_voices=paid_voices,
                    )
                elif mode == "story":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_story_from_image,
                    )
                elif mode == "joke":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_joke_from_image,
                    )
                elif mode == "prophecy":
                    process_spooky_spell(
                        strict=once,
                        paid_voices=paid_voices,
                        generate=spell_generator.generate_prophecy_from_image,
                    )
                else:
                    process_spooky_spell(strict=once, paid_voices=paid_voices)
                if once:
                    print("Smoke test PASSED.")
                    return

            time.sleep(0.05)

    except KeyboardInterrupt:
        pass

    finally:
        wait_for_stage_dump()  # Don't cut power to the motor mid-stroke.
        light_control.leds_off()
        GPIO.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the interactive Halloween cauldron controller."
    )
    parser.add_argument(
        "--mode",
        choices=("react", "request", "story", "joke", "prophecy"),
        default="react",
        help="react: spell about whatever is dropped. request: the witches name "
        "ingredients to fetch. story: a short spooky story about whatever is "
        "dropped. joke: a fun, spooky joke about whatever is dropped. "
        "prophecy: a campy, over-dramatic fortune about whatever is dropped "
        "(default: react).",
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
