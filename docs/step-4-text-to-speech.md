# Step 4: Testing Text-to-Speech

```bash
.venv/bin/python voice_generator.py --text "Bubble and toil, and cauldron boil, a silly little spell for you!"
```

This speaks the line in each of the three witch voices and writes `generated/violet.mp3`, `generated/amber.mp3`, and `generated/hazel.mp3`.
Passing `--spell spell.json` instead reads one line per witch from a spell file.

`--role amber` limits it to a single voice and a single request.
`--sequential` issues the three requests one at a time, which avoids the free tier's concurrent-request limit.

On the free tier, a `voice_id` your key cannot address returns HTTP 402.
List the voices your key can use and set the three `premade` IDs on `FREE_VOICES` in `voice_generator.py`:

```bash
.venv/bin/python voice_generator.py --list-voices
```

Play the results back (`mpg123` was installed in [Step 2](step-2-dependencies-and-api-keys.md)):

```bash
mpg123 generated/violet.mp3 generated/amber.mp3 generated/hazel.mp3
```

[← Previous: Step 3: Testing Audio Playback](step-3-audio-playback.md) · [↑ Back to README](../README.md) · [Next: Step 5: Testing the Webcam →](step-5-webcam.md)
