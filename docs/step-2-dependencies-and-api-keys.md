# Step 2: Installing Dependencies and API Keys

## Virtualenv

Install every system package the project needs in one pass: `python3-venv` for the virtualenv itself, `python3-rpi.gpio` and `python3-pygame` for GPIO and audio, and `fswebcam` and `mpg123` (the command-line tools used for bench testing in [Step 4](step-4-text-to-speech.md) and [Step 5](step-5-webcam.md)).

```bash
sudo apt update
sudo apt install -y python3-venv python3-rpi.gpio python3-pygame fswebcam mpg123
```

The project itself runs from a virtualenv.
Creating it with `--system-site-packages` lets it reuse the apt builds of `python3-rpi.gpio` and `python3-pygame` above instead of compiling them on the Pi.

```bash
cd ~/cauldron
python3 -m venv --system-site-packages .venv
.venv/bin/pip install opencv-python google-genai aiohttp rpi_ws281x
```

The `pip` step can take a few minutes, mostly for `opencv-python`.
From here on, run project scripts with `.venv/bin/python <script>` rather than `python3 <script>`.

Everything runs as your normal user; nothing needs `sudo`.
The LED ring is driven over SPI ([Step 8](step-8-led-ring.md)), which avoids the root-only `/dev/mem` access that the PWM method requires.
GPIO for the IR sensor goes through the `gpio` group, and the camera, APIs, and audio are all unprivileged.
Running as yourself is also what keeps audio working, since the Pi's sound server is per-user and a root process cannot reach it.

Avoid `sudo pip install --break-system-packages` into the system Python.
On Debian and Raspberry Pi OS it collides with apt-managed packages; for example, it cannot upgrade `typing_extensions` because the apt copy ships no uninstall record.

## API Keys

1. Create a **Gemini** API key at [aistudio.google.com](aistudio.google.com).
2. Create an **ElevenLabs** API key at [elevenlabs.io](elevenlabs.io), under Developers then API Keys.
   Grant it Text to Speech access and Voices read access.
3. Add both keys to your shell profile:
   ```bash
   nano ~/.bashrc
   ```
   ```bash
   export GEMINI_API_KEY="your-gemini-key"
   export ELEVENLABS_API_KEY="your-elevenlabs-key"
   ```
4. Reload the profile and confirm the values are set:
   ```bash
   source ~/.bashrc
   echo "$GEMINI_API_KEY" "$ELEVENLABS_API_KEY"
   ```

Because nothing runs under `sudo`, every script picks these up from your shell with no further setup.

[← Previous: Step 1: Setting Up Raspberry Pi OS](step-1-raspberry-pi-os.md) · [↑ Back to README](../README.md) · [Next: Step 3: Testing Audio Playback →](step-3-audio-playback.md)
