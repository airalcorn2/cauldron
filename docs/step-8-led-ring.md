# Step 8: Testing the LED Ring

1. Seat the 74AHCT125 level shifter on the mini breadboard, straddling the center gap.
   Confirm orientation from the notch or dot next to pin 1 (14-pin DIP: pin 1 and pin 14 are diagonally opposite corners).
2. Wire the 5 V row (same row as chip pin 14):
   - Chip **pin 14 (VCC)** — already seated here
   - External 5 V supply **+**
   - LED strip's **red (5 V)** wire — I had to cut off the switch the strip came with and splice on jumper wires.
3. Wire the ground row (same row as chip pin 7):
   - Chip **pin 7 (GND)** — already seated here
   - External 5 V supply **−**
   - LED strip's **white (GND)** wire
   - Pi **GND** (I used physical pin 20)
   - Jumper from chip **pin 1 (1OE)** — permanently enables buffer 1

   Tying the Pi's ground into this same row matters: skipping it causes flickering and random behavior, since the 3.3 V signal from the Pi needs a shared reference with the chip's 5 V side.
4. Wire the data path — this is the one connection that isn't just "join a row":
   - Pi **GPIO 10 / SPI0 MOSI** (physical pin 19) → chip **pin 2 (1A)**, the buffer's input
   - Chip **pin 3 (1Y)**, the buffer's output → LED strip's **teal (data)** wire

   GPIO 10 is used instead of a PWM pin (like GPIO 18) so the LED driver runs over SPI without needing root.
5. Enable SPI and pin the core clock.
   On the Pi 4 the core clock must be held constant or the SPI bit-timing drifts and the ring flickers or shows wrong colors.
   ```bash
   sudo raspi-config   # Interface Options -> SPI -> Yes
   ```
   In `/boot/firmware/config.txt`, remove any `dtoverlay=nospi...` or `disable-spi` line and add:
   ```
   core_freq_min=500
   ```
   Reboot, then confirm the device node exists:
   ```bash
   ls -l /dev/spidev0.0
   ```
   Check with `groups` that you are in `spi` and `gpio`; no `sudo` is needed to use the device.
   `rpi_ws281x` is a hard dependency installed in [Step 2](step-2-dependencies-and-api-keys.md), so run that `pip install` first if the LED scripts fail to import.
   Because the ring no longer uses PWM, the Pi's onboard audio can stay enabled.
6. Run the LED test:
   ```bash
   .venv/bin/python light_control.py leds
   ```

The ring should cycle red, green, blue, then turn off.
The same script has `color <violet|amber|hazel>` (or `color R G B`) and `flicker` subcommands for the other effects.

## Circuit Summary

1. The Pi generates the WS2812B timing pattern in software, output as a 3.3 V signal on GPIO 10.
2. That signal enters the 74AHCT125 at pin 2, which reads it and regenerates the identical on/off pattern at pin 3, but referenced to the chip's own 5 V supply — so it comes out as a full 5 V signal.
3. That boosted 5 V signal travels to the strip's data wire, which is what the WS2812B pixels need to reliably register the signal as valid.
4. Power for both the strip and the chip itself comes from the external 5 V supply, shared across a common 5 V rail and a common ground rail.
5. The Pi's own ground is tied into that same ground rail so the 3.3 V signal it sends has a consistent reference point relative to the 5 V logic on the other side of the chip.

## Troubleshooting

- Colors are swapped: some WS2812B variants are wired GRB rather than RGB, which is a one-line change in `light_control.py`.
- Nothing lights: recheck the level-shifter wiring and that the ring has its own 5 V supply sharing a common ground.
- `Can't open /dev/mem` or a permission error: the library took the PWM path.
  Confirm `StripConfig.pin` is `10` in `light_control.py` and that `/dev/spidev0.0` exists.
- Nothing lights even with wiring, power, and SPI all confirmed good: the 74AHCT125 itself may be damaged — this happened after a miswiring incident.
  A telling symptom is that the problem follows you when you move the data line to a different buffer channel on the same chip (e.g., pins 4/5/6 instead of 2/3/1), since that's one chip with four otherwise-independent buffers failing identically.
  Swapping the chip for a fresh one resolved it.

[← Previous: Step 7: Testing the IR Break-Beam Sensor](step-7-ir-sensor.md) · [↑ Back to README](../README.md) · [Next: Step 9: Testing the Stage Actuator →](step-9-stage-actuator.md)
