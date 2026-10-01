# Step 9: Testing the Stage Actuator

The actuator sits below the staging area.
Once the tray photo is taken, it extends to tip the stage and dump the items out the back of the table, then retracts to reset it.

1. Wire the actuator's driver board to the Pi:
   - Driver board power input to its own 12 V supply, not the Pi.
   - Driver board **GND** to Pi **GND** (any free ground pin), tying the grounds together.
   - Driver board **IN1** ("extend") to Pi **GPIO 16** (physical pin 36).
   - Driver board **IN2** ("retract") to Pi **GPIO 26** (physical pin 37).
2. Run a single stroke first, at a safe distance from moving parts:
   ```bash
   .venv/bin/python actuator.py extend
   ```
   If the stage tips the wrong way (or the actuator retracts instead of extending), swap `EXTEND_PIN` and `RETRACT_PIN` in `actuator.py`.
3. Run the full cycle used during a live round:
   ```bash
   .venv/bin/python actuator.py dump
   ```
   This extends, pauses `DWELL_SEC` for items to clear, then retracts.
   Adjust `STROKE_SEC` and `DWELL_SEC` in `actuator.py` to match your actuator's actual stroke time.

## Circuit Summary

1. GPIO 16 and GPIO 26 are the Pi's only outputs to the driver board — each is a 3.3 V logic signal telling the board which direction to run the motor, never the motor's actual current.
2. The driver board sits between that 3.3 V logic and the actuator's 12 V motor leads: driving IN1 (GPIO 16) high switches 12 V one way across the leads to extend the actuator, driving IN2 (GPIO 26) high switches it the other way to retract, and `actuator.py`'s `_drive()` always sets the *other* pin low first so both are never driven at once.
3. The 12 V that actually moves the actuator comes entirely from its own supply, not the Pi — the Pi only ever sources the milliamp-level logic signal the driver board reads.
4. The driver board's ground, the 12 V supply's ground, and the Pi's ground are all tied together, which is what lets a 3.3 V HIGH from the Pi mean the same thing to the board as it steps up to the 12 V side.

[← Previous: Step 8: Testing the LED Ring](step-8-led-ring.md) · [↑ Back to README](../README.md) · [Next: Step 10: Running the End-to-End Smoke Test →](step-10-smoke-test.md)
