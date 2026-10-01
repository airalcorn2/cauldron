"""Linear actuator that tips the capture stage to dump its contents.

Mounted below the staging area. Once the tray photo is taken, the actuator
extends to tip the stage and dump the items out the back of the table, holds
at full extension so they clear, then retracts to reset the stage.

Run directly to bench-test it:

    .venv/bin/python actuator.py extend                # One extend stroke.
    .venv/bin/python actuator.py retract               # One retract stroke.
    .venv/bin/python actuator.py dump                  # Extend, dwell, retract.
    .venv/bin/python actuator.py extend --duration 2   # Shorter stroke.

If the stage tips the wrong way, swap EXTEND_PIN and RETRACT_PIN below.
"""

from __future__ import annotations

import argparse
import time

import RPi.GPIO as GPIO

EXTEND_PIN = 16  # GPIO 16 (physical pin 36).
RETRACT_PIN = 26  # GPIO 26 (physical pin 37).

STROKE_SEC = 4.0  # Time for one full extend or retract stroke.
DWELL_SEC = 2.0  # Hold at full extension so items fully clear before retracting.


def setup() -> None:
    """Configure the control pins. Safe to call alongside other modules' setup()."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(EXTEND_PIN, GPIO.OUT)
    GPIO.setup(RETRACT_PIN, GPIO.OUT)
    GPIO.output(EXTEND_PIN, GPIO.LOW)
    GPIO.output(RETRACT_PIN, GPIO.LOW)


def _drive(active_pin: int, other_pin: int, duration: float) -> None:
    """Energize one direction for ``duration`` seconds, then de-energize it."""
    GPIO.output(other_pin, GPIO.LOW)
    GPIO.output(active_pin, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(active_pin, GPIO.LOW)


def extend(duration: float = STROKE_SEC) -> None:
    """Extend the actuator to tip the stage. Blocks for ``duration`` seconds."""
    _drive(EXTEND_PIN, RETRACT_PIN, duration)


def retract(duration: float = STROKE_SEC) -> None:
    """Retract the actuator to reset the stage. Blocks for ``duration`` seconds."""
    _drive(RETRACT_PIN, EXTEND_PIN, duration)


def dump_stage() -> None:
    """Tip the stage, hold at full extension for the items to clear, then reset it. Blocks."""
    print("Dumping the stage.")
    extend()
    time.sleep(DWELL_SEC)
    retract()
    print("Stage reset.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bench-test the stage-tipping actuator."
    )
    parser.add_argument(
        "action",
        choices=("extend", "retract", "dump"),
        help="extend/retract: run one stroke by itself. "
        "dump: extend, dwell, retract, as in a live round.",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=STROKE_SEC,
        help=f"Stroke length in seconds for extend/retract (default: {STROKE_SEC:g}).",
    )
    args = parser.parse_args()

    setup()
    if args.action == "extend":
        print(f"Extending for {args.duration:g}s.")
        extend(args.duration)
    elif args.action == "retract":
        print(f"Retracting for {args.duration:g}s.")
        retract(args.duration)
    else:
        dump_stage()
    print("Done.")


if __name__ == "__main__":
    main()
