# Step 10: Running the End-to-End Smoke Test

With every component wired and passing its own test, run one full cycle:

```bash
.venv/bin/python cauldron_controller.py --once
```

It waits for a single beam break, then runs the whole sequence once: flash the lights, capture a photo, tip the stage to dump the items, generate the spell, synthesize the three voices, and play them back with synced lighting.
Each stage prints as it runs.
The normal loop logs a failed stage and waits for the next trigger, but `--once` aborts on any failure and exits non-zero, so the broken component is obvious.

To smoke-test request mode, add `--mode request`; for story mode, add `--mode story`:

```bash
.venv/bin/python cauldron_controller.py --once --mode request
.venv/bin/python cauldron_controller.py --once --mode story
```

The stages of request mode can also be exercised individually:

```bash
.venv/bin/python spell_generator.py --request > recipe.json   # Invent a recipe.
.venv/bin/python spell_generator.py --outcome recipe.json test.jpg
```

Once it passes, run the installation loop in whichever mode you want:

```bash
.venv/bin/python cauldron_controller.py                 # React mode.
.venv/bin/python cauldron_controller.py --mode request  # Request mode.
.venv/bin/python cauldron_controller.py --mode story     # Story mode.
```

[← Previous: Step 9: Testing the Stage Actuator](step-9-stage-actuator.md) · [↑ Back to README](../README.md)
