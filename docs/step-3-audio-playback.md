# Step 3: Testing Audio Playback

Connect speakers to the Pi's 3.5 mm audio jack — any speakers (or headphones) with that jack will work.

`audio.py` wraps playback for the controller.
Use it to confirm sound reaches the speaker:

```bash
.venv/bin/python audio.py --loop 5   # Loop the bubbling effect for 5 seconds.
```

Once you've completed [Step 4](step-4-text-to-speech.md) below and have files in `generated/`, you can play one the same way:

```bash
.venv/bin/python audio.py generated/violet.mp3
```

## Troubleshooting

- Witch lines sound broken up / stutter mid-word, even though the same file plays cleanly elsewhere: this was pygame's SDL mixer itself, not the generated audio or its volume settings.
  `play_file()` shells out to `mpg123` (mp3) / `aplay` (wav) instead for exactly this reason; the bubbling/laugh ambience loops are unaffected and still use pygame.
- `aplay: audio open error`: this Pi has multiple ALSA sound cards (HDMI outputs, the USB webcam's audio, and the 3.5 mm jack), and the bare `default` device can resolve to one of the unusable ones.
  `audio.py`'s `ALSA_DEVICE` pins the jack explicitly (`plughw:Headphones,0`); confirm that card still exists with `aplay -l` if this ever changes (e.g. a different Pi model or audio HAT).

[← Previous: Step 2: Installing Dependencies and API Keys](step-2-dependencies-and-api-keys.md) · [↑ Back to README](../README.md) · [Next: Step 4: Testing Text-to-Speech →](step-4-text-to-speech.md)
