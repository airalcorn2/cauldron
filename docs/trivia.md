# Trivia

A two-team horror/Halloween trivia game, hosted from this Pi and played from everyone's own phones.
This page is unnumbered and separate from the [Setting Up](../README.md#setting-up) sequence — it's an optional party activity, not part of getting the core prop running.

## Setup

One extra dependency beyond the core prop's, for generating the group screen's QR codes:

```bash
.venv/bin/pip install qrcode
```

## Preparing the Question Bank

Run once, well ahead of time:

```bash
.venv/bin/python trivia_data.py
```

This downloads a [public Jeopardy! clue dataset](https://github.com/jwolle1/jeopardy_clue_dataset) and filters it down to horror/Halloween-adjacent categories — usually well over a thousand clues.
Since this is a one-night event rather than a standing feature, it then samples that pool down to a manageable `--count` (30 by default — pass e.g., `--count 20` for a shorter night), assigns each sampled clue a single witch to voice it, and pre-synthesizes just those into `generated/trivia/`.
That sample and witch assignment are fixed (not re-rolled each run), so re-running the script — after adding to `excluded_ids.json`, say — only synthesizes whatever's newly missing rather than regenerating everything.
The result is written to `trivia/horror_clues.json`.

For a second (or third, ...) party night with entirely new questions, don't touch the seed — pass a new `--batch` number instead:

```bash
.venv/bin/python trivia_data.py --batch 1
```

Batches never share a clue with each other, so `--batch 1` is guaranteed to be all-new questions compared to `--batch 0` (the default), `--batch 2` all-new again compared to both, and so on; re-running the same `--batch` number always reproduces that same night's set.

### Reviewing the Question Bank

Automated keyword filtering isn't perfect: it'll pull in the occasional category that uses a horror word as a pun on something unrelated (e.g., a category about advertising history that happens to be titled after "Tales from the Crypt"), or something you just don't want asked at your party.
`trivia/horror_clues.json` only holds the small sampled set actually in play, so it's quick to skim in full — note the `id` of anything you want dropped and add it to `trivia/excluded_ids.json`:

```json
["abc123def456", "789abc012def"]
```

Re-run `.venv/bin/python trivia_data.py` afterward — excluded ids are subtracted from the candidate pool before sampling, and `id`s are stable (derived from the category and clue text), so this file keeps working across re-runs even if the filter itself changes.
Because dropping an id reduces the pool, re-sampling can pick a different clue to fill that slot — the other clues already in your set stay the same, but don't expect the vacated slot to carry over any audio you'd already generated for whatever happens to land there next.

To iterate on the keyword filter itself (in `trivia_data.py`'s `INCLUDE_KEYWORDS`/`EXCLUDE_KEYWORDS`) without waiting on ElevenLabs each time:

```bash
.venv/bin/python trivia_data.py --skip-audio
```

## Hosting a Round

Unlike the data-prep step above, this part does need live network access -- every answer is graded by Gemini as it comes in (see the README's Trivia section), not checked locally, so the Pi needs a working internet connection for the whole round, not just ahead of time.

```bash
.venv/bin/python trivia_game.py
```

This prints two addresses:

- A **group** screen — put this on a TV or other shared display.
  It opens on the game's title and one QR code per team; once the round starts, it switches to a live scoreboard, the current category/clue/points, and the outcome of each attempt.
  It never collects input itself.
- **Team** pages — one per team, each reached by scanning that team's QR code on the group screen.
  Scanning joins automatically (the link itself says which team); no name or button tap needed.
  More than one phone per team is fine — whichever one submits first locks in that team's answer for the active clue, and the rest just see "a teammate already answered."

Each clue is read aloud by whichever witch `trivia_data.py` assigned it (randomly, but fixed per clue -- not freshly re-rolled each playthrough); the team whose turn it is answers free text on their phone -- just the answer itself (e.g., "a poltergeist"), not phrased as a question.
That same witch then judges the answer and delivers an in-character line reacting to it specifically, before the next clue goes out.
See the [README's Trivia section](../README.md#trivia) for the turn/steal rules, and `trivia_game.py` itself for the exact implementation.

If the two teams are still tied once the main pool runs out, the round doesn't just end there: it goes to sudden death.
A witch reads one numeric question, picked at random from a bank of 50 hand-curated, individually sourced questions (see `TIEBREAKER_QUESTIONS` in `trivia_game.py`) -- both teams' phones open at once, not alternating turns, and whichever guess lands closer to the real answer wins outright.
A few entries (franchise film counts, the 2023 Halloween-spending figure) are scoped to a specific date or year rather than claimed as eternally true -- worth a quick re-check against their cited source before reusing this bank many years from now.
The real answer and its source are revealed either way, so the room can see it wasn't made up.
If neither guess parses as a number, or both land exactly as close (vanishingly unlikely), the winner's a genuine coin flip rather than defaulting to Team 1.

Once a winner's decided, the game doesn't just end on a scoreboard -- all three witches each deliver a short closing line, together recapping how that specific game actually went (the final score, a category someone dominated, whether a steal or the tiebreaker settled it, and so on).
These are written live by Gemini per round (see `generate_finale()`), not hand-written, since what they reference is different every time; the group screen and team pages both show the text as each witch speaks it.

## Troubleshooting

- **"not found -- run trivia_data.py once first"**: exactly what it says — the prep step above hasn't been run yet, or `trivia/horror_clues.json` got deleted.
- **A phone can't reach its team page / a QR code won't scan**: confirm the phone is on the same Wi-Fi network as the Pi, not a different one (including a phone's own cellular data) -- a QR code pointing at `localhost` or the wrong network's address won't resolve from another device.
  `trivia_game.py` also prints each team's plain URL at startup as a fallback to type in by hand.
- **An answer seems to have been graded wrong**: grading is Gemini's call (`trivia_game.judge_answer()`), not a local check or a human judge -- it's told to be lenient about phrasing, but an occasional surprising call is possible with any judge, human or not.
- **A round stalls or a judgment takes a long time**: `judge_answer()` is a live Gemini call per answer, so the Pi's internet connection has to hold up for the whole round, not just during setup -- check connectivity if things hang past a few seconds (the LED ring flickers while Gemini is deliberating, so a *dead* ring during that wait points at a crash, not just slowness).
