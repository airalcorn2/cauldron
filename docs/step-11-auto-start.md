# Step 11: Restarting via the Gamepad

So far, `cauldron_controller.py` only runs for as long as the terminal session that started it.
This step makes it a proper background service, and gives it an on switch anyone can reach: pressing any button on the gamepad, with nothing more than the gamepad plugged in and the Pi powered.
That means if you stop it (to quiet it down, swap batteries, whatever), your kids can start it again themselves without touching a keyboard.

## Install Triggerhappy

[triggerhappy](https://github.com/wertarbyte/triggerhappy) is a small daemon that runs a command when a key or gamepad button is pressed.
It's the "always running, lightweight" half of this setup.

```bash
sudo apt install -y triggerhappy
```

## Create the Cauldron Service

Move the API keys out of `~/.bashrc` and into a dedicated file, since systemd services don't read your shell profile:

```bash
cp .env.example .env
nano .env   # Fill in your real GEMINI_API_KEY and ELEVENLABS_API_KEY.
```

Install the unit that runs the show itself:

```bash
sudo cp systemd/cauldron.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Confirm it works before wiring up the button:

```bash
sudo systemctl start cauldron.service
sudo systemctl status cauldron.service   # Should show "active (running)".
journalctl -u cauldron.service -f        # Live log; Ctrl-C to stop watching.
sudo systemctl stop cauldron.service
```

## Wire Up the Buttons

If your gamepad isn't the one this project was built with, first find the raw button codes for all of its buttons:

```bash
sudo apt install -y evtest
evtest /dev/input/by-id/*-event-joystick   # Pick your pad from the list if prompted.
```

Press each button in turn and note the `code` name it prints for each one (e.g., `BTN_BASE4`).
`systemd/triggerhappy-cauldron.conf` already has one line per button for this project's pad -- edit it to match your own pad's codes, one line per button, if yours differs.

```bash
sudo cp systemd/triggerhappy-cauldron.conf /etc/triggerhappy/triggers.d/
sudo systemctl restart triggerhappy
```

triggerhappy runs trigger commands as the unprivileged `nobody` user, and `nobody` can't start a system service without a logged-in session to authenticate -- `journalctl -u triggerhappy` would show "Interactive authentication required" otherwise.
A narrow polkit rule grants exactly the one permission needed (letting `nobody` run `start`, and only `start`, on `cauldron.service`):

```bash
sudo cp systemd/60-cauldron-trigger.rules /etc/polkit-1/rules.d/
sudo systemctl restart polkit
```

Stop the cauldron (`sudo systemctl stop cauldron.service`) if it's still running from the test above, then press any button on the gamepad -- the show should come back up on its own within a few seconds.

## Killing It

When you want to quiet the cauldron down (swap batteries, go to bed, whatever), stop it the same way you started it in the test above:

```bash
sudo systemctl stop cauldron.service
```

That's the one command for this -- don't `kill`/`pkill` the Python process directly.
`systemctl stop` sends the process a clean shutdown signal, so it finishes any actuator motion in progress and releases the GPIO pins properly, rather than just dying mid-stroke.
It also takes effect in well under a second, so there's no need to wait around after running it.
Once it's stopped, any button on the gamepad brings it right back, same as in the test above.

## Optional: Start Automatically at Boot Too

The cauldron only starts via a gamepad button by default, which keeps one consistent way to turn it on.
If you'd also like it running immediately after every boot (no button press needed for that first start):

```bash
sudo systemctl enable cauldron.service
```

[← Previous: Step 10: Running the End-to-End Smoke Test](step-10-smoke-test.md) · [↑ Back to README](../README.md)
