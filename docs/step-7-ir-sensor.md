# Step 7: Testing the IR Break-Beam Sensor

1. Wire the **transmitter** (two wires):
   - Red to Pi **5 V** (I used physical pin 2)
   - Black to Pi **GND** (I used physical pin 14)
2. Wire the **receiver** (three wires), using 3.3 V rather than 5 V to keep the GPIO pin safe:
   - Red to Pi **3.3 V** (I used physical pin 1)
   - Black to Pi **GND** (I used physical pin 9)
   - Signal (white or yellow) to Pi **GPIO 17** (physical pin 11)
3. Face the transmitter and receiver a few inches apart with a clear line of sight.
   This is a bench test; it does not need to be mounted to the table yet.
4. Run the sensor watch:
   ```bash
   .venv/bin/python ir_sensor.py
   ```
5. Wave a hand through the beam.
   You should see "Beam broken!" on each break.

## Circuit Summary

1. The transmitter is a bare IR LED powered continuously from the Pi's 5 V rail, sending a steady beam across the gap toward the receiver.
2. The receiver's internal detector and comparator hold its digital output HIGH while the beam reaches it, and pull it LOW the instant something blocks it.
3. The receiver runs off 3.3 V rather than 5 V specifically so its HIGH output can never exceed the Pi's GPIO input range — a 5 V receiver output risks damaging the pin.
4. `ir_sensor.py` reinforces that idle-HIGH state with the Pi's own internal pull-up (`pull_up_down=GPIO.PUD_UP`), so the line reads a clean HIGH even if the receiver's own output floats; `beam_broken()` is just a read of `GPIO.LOW` on GPIO 17.
5. The transmitter, receiver, and Pi all share the same ground, which is what lets the Pi's 3.3 V logic and the receiver's signal agree on what HIGH and LOW mean.

[← Previous: Step 6: Testing the Vision Model](step-6-vision-model.md) · [↑ Back to README](../README.md) · [Next: Step 8: Testing the LED Ring →](step-8-led-ring.md)
