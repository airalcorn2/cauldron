"""Kid-friendly Halloween/monster trivia for trivia_game.py, generated
fresh by Gemini rather than filtered from a real-world dataset -- see
trivia_data.py, the sibling script this stands in for (same party game,
a different crowd: trivia_data.py's Jeopardy! clues skew adult, with
plenty of serial killers and real tragedies among the "horror" category
matches, nowhere near appropriate for kids).

Writes to the exact same trivia/horror_clues.json and generated/trivia/
paths trivia_data.py does, so switching the question bank ahead of a
kids' party vs. an adults' one is just "run this script instead of
that one" -- trivia_game.py itself needs no changes either way. Running
this OVERWRITES whichever bank is currently there; run trivia_data.py
again to switch back.

There's no vetted source to check these against (unlike trivia_data.py's
real dataset or trivia_tiebreakers.py's hand-cited facts), so give the
output a skim before a live party -- an easy sanity check that's not
automated here, kept deliberately simple per the user's call.

Run directly, once, well ahead of a kids' party:

    .venv/bin/python trivia_kids.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
from dataclasses import asdict, replace
from pathlib import Path

from google import genai
from google.genai import types

from trivia_data import AUDIO_CACHE_DIR, OUTPUT_PATH, Clue
from voice_generator import VOICES, generate_to_path
from witches import Witch

CLUE_COUNT = 24
"""Split evenly between round 1 and round 2 (see generate_round()), same
reasoning as trivia_data.CLUE_COUNT: a one-night event doesn't need a
huge bank, and keeping both round-halves even avoids one team getting
an extra turn at a round's point value (see trivia_data.select_clues()'s
docstring for the full reasoning, which applies identically here)."""

POINTS_BY_ROUND = {1: 1, 2: 2}
"""Same scheme as trivia_data.POINTS_BY_ROUND -- not imported, since
this module invents its own clues rather than filtering a shared dataset
row format, but the game expects the same round-1-is-easier/round-2-is-
harder, points-track-round shape either way."""

AUDIENCE_NOTE = (
    "The audience is kids, roughly ages 6-10, at a Halloween party. Keep "
    "it trick-or-treat fun, not horror-movie scary: friendly monsters, "
    "costumes, candy, pumpkins, silly ghosts, cute bats, and spiders are "
    "all great. Absolutely no gore, violence, death, real-world tragedy, "
    "or anything that could actually frighten or disturb a young child."
)
"""Edit this to retarget the age range or tone -- every generation
prompt below includes it verbatim."""

_GEMINI_CLIENT = genai.Client(
    http_options=types.HttpOptions(
        # Milliseconds, not seconds.
        timeout=20_000,
        retry_options=types.HttpRetryOptions(
            attempts=4,
            initial_delay=1.0,
            max_delay=10.0,
            exp_base=2.0,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    )
)
"""Its own client/model, same reasoning as trivia_data.py's
_GEMINI_CLIENT and trivia_game.py's: these sibling trivia modules stay
free of a dependency on each other. One-time prep, never live, so the
generous retry budget costs nothing."""
_GEMINI_MODEL = "gemini-3.8-flash"
_GENERATION_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.9,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
"""Creative enough for varied categories/phrasing, but not pushed as hot
as spell_generator's creative calls -- a trivia answer still needs to be
a single, unambiguous fact, not a flourish."""
_TAG_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.2,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
"""temperature favors faithful tag insertion over creative rewriting --
same config trivia_data.py uses for the same job."""
_TAG_PATTERN = re.compile(r"\[[^\]]*\]")


def _generation_prompt(round_number: int, count: int, avoid: list[str]) -> str:
    difficulty = (
        "very easy -- a kindergartner or first-grader should have a real "
        "shot at these"
        if round_number == 1
        else "a little trickier, but still squarely kid-level -- think "
        "a curious 8-10 year old, not an adult"
    )
    avoid_block = ""
    if avoid:
        avoid_list = "; ".join(avoid)
        avoid_block = f"""
This question bank already uses these answers elsewhere -- do not reuse
any of them, or anything that's just a reworded version of the same
fact: {avoid_list}.
"""
    return f"""
{AUDIENCE_NOTE}

Write {count} original Halloween/monster-themed trivia questions for
round {round_number} of a trivia game. Difficulty: {difficulty}.

Each question needs:
  - "category": a short (2-4 word) theme name, varied across the set
    (don't repeat the same category twice).
  - "clue": a single, plainly worded question (not a Jeopardy-style
    statement) with exactly one unambiguous correct answer.
  - "response": the correct answer, as short as it can be (a word or a
    few words).

Vary the topics across the set -- pumpkins, costumes, candy, bats,
spiders, witches, ghosts, mummies, vampires, werewolves, haunted
houses, trick-or-treating traditions, and friendly monster-movie
trivia are all fair game, but don't lean on any one of them repeatedly.
Every "response" in this set must be a different answer from every
other one in it -- never write two questions with the same correct
answer, even if they're phrased differently or ask about a different
fact (e.g., candy corn's shape vs. its colors still both answer "candy
corn" -- pick at most one candy-corn question, if any).
{avoid_block}
Return ONLY a valid JSON array of exactly {count} objects in this
shape, with no extra text:
[{{"category": "...", "clue": "...", "response": "..."}}, ...]
"""


def _clue_id(category: str, clue: str) -> str:
    """Same stable-hash approach as trivia_data._clue_id(), though
    reproducibility matters less here: nothing re-reads an id across
    runs (there's no excluded_ids.json-style file keyed on it), it just
    needs to be a stable, short filename for this run's audio cache."""
    digest = hashlib.sha256(f"{category}\x1f{clue}".encode()).hexdigest()
    return digest[:12]


def generate_round(round_number: int, count: int, avoid: list[str]) -> list[Clue]:
    """Ask Gemini for ``count`` kid-friendly clues for one round, and
    turn the reply into game-ready Clue rows (witch assignment and
    Audio Tag markup happen later, in main()).

    ``avoid`` is the responses already used by an earlier round in this
    same bank -- round 1 and round 2 are separate Gemini calls, so
    without this, each call only dedupes against itself and can easily
    land on the same answer (e.g., "garlic" for both a round-1 and a
    round-2 vampire question) with no visibility into the other call.
    """
    response = _GEMINI_CLIENT.models.generate_content(
        model=_GEMINI_MODEL,
        contents=_generation_prompt(round_number, count, avoid),
        config=_GENERATION_CONFIG,
    )
    if response.text is None:
        raise SystemExit(f"Gemini returned no text for round {round_number}.")
    rows = json.loads(response.text)
    points = POINTS_BY_ROUND[round_number]
    clues = []
    for row in rows:
        category = str(row["category"]).strip()
        clue_text = str(row["clue"]).strip()
        clue_resp = str(row["response"]).strip()
        if not (category and clue_text and clue_resp):
            continue
        clues.append(
            Clue(
                _clue_id(category, clue_text),
                category,
                clue_text,
                clue_resp,
                points,
                round_number,
            )
        )
    return clues


def _even_count(n: int) -> int:
    """Round down to the nearest even number."""
    return n - (n % 2)


def generate_clues(count: int) -> list[Clue]:
    """Generate ``count`` clues total, split evenly between round 1 and
    round 2 (each half forced even too -- see CLUE_COUNT's docstring),
    then assign each one a witch to voice it."""
    total = _even_count(count)
    half = total // 2
    round_targets = (half, half) if half % 2 == 0 else (half - 1, half + 1)

    clues: list[Clue] = []
    used_responses: list[str] = []
    for round_number, target in zip((1, 2), round_targets):
        round_clues = generate_round(round_number, target, used_responses)
        if len(round_clues) < target:
            raise SystemExit(
                f"Gemini only returned {len(round_clues)} usable clue(s) for "
                f"round {round_number}, needed {target} -- try again."
            )
        round_clues = round_clues[:target]
        clues.extend(round_clues)
        used_responses.extend(c.response for c in round_clues)

    _warn_about_duplicate_responses(clues)
    return [replace(clue, witch=random.choice(list(Witch))) for clue in clues]


def _warn_about_duplicate_responses(clues: list[Clue]) -> None:
    """Flag (not fix) any response that still slipped through as a
    duplicate despite generate_round()'s "avoid" list -- an LLM
    following an instruction isn't a guarantee, so this is a visible
    heads-up to catch and reword by hand, same spirit as this module's
    "give the output a skim" guidance rather than a hard failure."""
    seen: dict[str, str] = {}
    for clue in clues:
        key = re.sub(r"\s+", " ", clue.response).strip().lower()
        if key in seen:
            print(
                f"Warning: duplicate answer {clue.response!r} in both "
                f"{seen[key]!r} and {clue.category!r} -- reword one by hand.",
                file=sys.stderr,
            )
        else:
            seen[key] = clue.category


def _strip_tags(text: str) -> str:
    return _TAG_PATTERN.sub("", text)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _annotation_prompt(clue_text: str) -> str:
    return f"""
Rewrite the following text for ElevenLabs' text-to-speech "Audio Tags" --
bracketed delivery directions like [stress on next word], [emphasized],
[dramatic pause], or [intriguing], inserted inline. Each tag shifts delivery
for the words that follow it, until the next tag or the end of the text.

Insert 1-3 such tags at whatever points would make this read more naturally
and with the right emphasis when spoken aloud by a theatrical Halloween
witch hosting a trivia game for kids. Do not change, add, remove, or
reorder a single word of the actual text -- only insert bracketed tags
between words.

Text: {clue_text!r}

Return ONLY a valid JSON object in this exact format, with no extra text:
{{"spoken": "...with tags inserted..."}}
"""


async def _annotate_for_speech(clue: Clue) -> Clue:
    """Same approach (and same fallback-on-mismatch safety net) as
    trivia_data._annotate_for_speech() -- duplicated rather than
    imported, per this module's own sibling-independence reasoning
    above."""
    response = await _GEMINI_CLIENT.aio.models.generate_content(
        model=_GEMINI_MODEL,
        contents=_annotation_prompt(clue.clue),
        config=_TAG_CONFIG,
    )
    if response.text is None:
        return replace(clue, spoken_clue=clue.clue)
    print(f"[gemini:annotate] {response.text}", file=sys.stderr)
    try:
        spoken = str(json.loads(response.text)["spoken"])
    except (json.JSONDecodeError, KeyError):
        return replace(clue, spoken_clue=clue.clue)
    if _normalized(_strip_tags(spoken)) != _normalized(clue.clue):
        print(f"Audio Tag rewrite changed clue {clue.id}'s wording -- skipped.")
        return replace(clue, spoken_clue=clue.clue)
    return replace(clue, spoken_clue=spoken)


async def _annotate_clues_for_speech(clues: list[Clue]) -> list[Clue]:
    """Sequential, not gathered -- same reasoning as ensure_voice_cache()
    below."""
    return [await _annotate_for_speech(clue) for clue in clues]


def _clue_audio_path(witch: Witch, clue_id: str) -> Path:
    return AUDIO_CACHE_DIR / f"{witch}_{clue_id}.mp3"


async def ensure_voice_cache(clues: list[Clue]) -> None:
    """Synthesize each clue's audio -- one witch per clue, whichever
    generate_clues() assigned it. Sequential: ElevenLabs' concurrent-
    request limit rejects a burst. Same shape as
    trivia_data.ensure_voice_cache()."""
    print(f"Synthesizing {len(clues)} trivia clue line(s).")
    for clue in clues:
        witch = Witch(clue.witch)
        spoken = clue.spoken_clue or clue.clue
        text = f"From {clue.category}, for {clue.points} point{'s' if clue.points != 1 else ''}: {spoken}"
        await generate_to_path(text, VOICES[witch], _clue_audio_path(witch, clue.id))
    print("Trivia voice cache ready.")


async def _prepare_audio(clues: list[Clue]) -> list[Clue]:
    clues = await _annotate_clues_for_speech(clues)
    await ensure_voice_cache(clues)
    return clues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate kid-friendly Halloween/monster trivia with "
        "Gemini and voice-cache it, in place of trivia_data.py's "
        "adult-oriented Jeopardy! clue bank."
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Only (re)write trivia/horror_clues.json; skip ElevenLabs "
        "synthesis. Useful while tuning the generation prompt.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=CLUE_COUNT,
        help=f"How many clues to generate (default: {CLUE_COUNT}).",
    )
    args = parser.parse_args()

    clues = generate_clues(args.count)

    if not args.skip_audio:
        clues = asyncio.run(_prepare_audio(clues))

    OUTPUT_PATH.write_text(json.dumps([asdict(c) for c in clues], indent=2) + "\n")
    print(
        f"{len(clues)} kid-friendly clues written to {OUTPUT_PATH} -- give "
        "them a skim before the party, then run trivia_game.py."
    )


if __name__ == "__main__":
    main()
