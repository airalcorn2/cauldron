"""One-time prep for the horror-trivia game: download a public Jeopardy!
clue dataset (https://github.com/jwolle1/jeopardy_clue_dataset -- its own
maintainer prefers not to credit where the clues were sourced from), filter
it down to horror/Halloween-adjacent clues, and pre-synthesize every
surviving clue's audio for all three witches.

Never run during a live round -- trivia_game.py only ever reads the output
of this script (trivia/horror_clues.json) and the audio cache it fills
(generated/trivia/), so the live show never waits on a network call, same
reason cauldron_controller._ensure_mode_select_cache() and
potion_play.ensure_cache() exist.

Run directly, once, well ahead of a party:

    .venv/bin/python trivia_data.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import random
import re
import sys
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from google import genai
from google.genai import types

from voice_generator import VOICES, generate_to_path
from witches import Witch

CLUE_COUNT = 30
"""This is a one-night event, not a standing feature -- no need to voice
every clue for all three witches (3x the ElevenLabs cost/time for
variety nobody will notice across one evening) or to carry the full
~1,300-clue pool (more than a single night's trivia round would ever get
through). CLUE_COUNT samples down to a manageable set; SAMPLE_SEED/
WITCH_SEED below keep that sample (and each clue's assigned witch)
stable across re-runs of this script, so re-running it (e.g., after
adding to excluded_ids.json) doesn't reshuffle the selection and throw
away already-synthesized audio. To get a whole new set of questions for
another night, leave these alone and pass a new --batch number instead
-- see select_clues()."""
SAMPLE_SEED = 0
"""Paired with WITCH_SEED below -- see CLUE_COUNT above."""
WITCH_SEED = 1
"""Paired with SAMPLE_SEED above -- see CLUE_COUNT above."""

DATASET_VERSION = 42
"""The dataset release to pull -- bump this to pick up newer seasons; it
drives every filename/URL below, all of which encode it the dataset's
own two different ways ("v42" for the release tag, "1-42" for the
archive/file names)."""
_ARCHIVE_NAME = f"jeopardy_dataset_seasons_1-{DATASET_VERSION}"

DATASET_URL = (
    "https://github.com/jwolle1/jeopardy_clue_dataset/releases/download/"
    f"v{DATASET_VERSION}/{_ARCHIVE_NAME}.zip"
)
TSV_MEMBER = f"{_ARCHIVE_NAME}/combined_season1-{DATASET_VERSION}.tsv"

TRIVIA_DIR = Path("trivia")
RAW_ZIP_PATH = TRIVIA_DIR / "raw" / f"{_ARCHIVE_NAME}.zip"
TSV_PATH = TRIVIA_DIR / "raw" / f"combined_season1-{DATASET_VERSION}.tsv"
OUTPUT_PATH = TRIVIA_DIR / "horror_clues.json"
EXCLUDED_IDS_PATH = TRIVIA_DIR / "excluded_ids.json"
"""Hand-reviewed escape hatch: clue ids to drop even though they matched
the keyword filter (e.g., "PITCHES FROM THE CRYPT" is about ad
campaigns, not horror -- a pun category the filter can't tell apart from
a real one). A plain JSON list of ids; starts out not existing, which is
the same as an empty list."""

AUDIO_CACHE_DIR = Path("generated/trivia")

INCLUDE_KEYWORDS = (
    "HALLOWEEN",
    "HORROR",
    "MONSTER",
    "VAMPIRE",
    "ZOMBIE",
    "GHOST",
    "WITCH",
    "HAUNT",
    "SPOOKY",
    "SLASHER",
    "SUPERNATURAL",
    "WEREWOLF",
    "FRANKENSTEIN",
    "DRACULA",
    "DEMON",
    "EXORCIS",
    "CREEPY",
    "SCARY",
    "FRIGHT",
    "PHANTOM",
    "CEMETERY",
    "GRAVEYARD",
    "GRAVESITE",
    "GRAVESIDE",
    "SKELETON",
    "MUMMY",
    "OCCULT",
    "PARANORMAL",
    "EDGAR ALLAN POE",
    "GOTHIC LITERATURE",
    "CRYPT",
    "POLTERGEIST",
)
"""Primary signal: category names are curator-written and thematically
coherent, so matching on the category (rather than the clue/response
text, which is noisier -- an unrelated category's clue can mention
"ghost" in passing) is precise. Found by actually running this filter
against the real dataset and hand-checking the categories it pulled in
(1,276 clues across 188 categories) -- not a guessed keyword list."""
EXCLUDE_KEYWORDS = (
    "SWITCH",
    "TWITCH",
    "DEMONSTRAT",
    "DEMON-STRAT",
    "DEMONYM",
    "VAMPIRE WEEKEND",
    "MONSTER TRUCK",
    "COOKIE MONSTER",
    "MONSTERS INC",
    "MONSTERS INK",
    "MONSTER MANUAL",
    "MONSTER.COM",
    "MONSTER ENERGY",
    "GRAVE MISTAKE",
    "BEYOND THE GRAVE",
    "CRYPTIC",
    "CRYPTO",
    "FANDEMONIUM",
)
"""Substring collisions the include list above produces on the real data
(e.g., "WITCH" inside "SWITCH", "DEMON" inside "DEMONSTRATION") plus a
few categories that use a horror word as a pun on something unrelated
(the band Vampire Weekend, Monster brand energy drinks). Checked against
the actual matched category list, not guessed."""
_INCLUDE_PATTERN = re.compile("|".join(INCLUDE_KEYWORDS), re.IGNORECASE)
_EXCLUDE_PATTERN = re.compile("|".join(EXCLUDE_KEYWORDS), re.IGNORECASE)

POINTS_BY_ROUND = {"1": 1, "2": 2}
"""Flat per-round point value -- the dataset's raw dollar figures swing
wildly across eras (inflation, value increases over the decades) and
aren't meaningful as absolute points for a casual game. Round 3 (Final
Jeopardy!) is dropped entirely below -- it's wager-based, which this
game doesn't model."""


@dataclass(frozen=True, slots=True)
class Clue:
    """One filtered, game-ready trivia clue."""

    id: str
    category: str
    clue: str
    """The clean text -- shown on screen and read to Gemini for
    judging."""
    response: str
    """The correct response (the dataset's "question" field)."""
    points: int
    round: int
    """Jeopardy! round (1 or 2) -- drives points, and the group page's
    "Round N" display."""
    witch: str = ""
    """Assigned by select_clues(); empty in the unsampled full pool."""
    spoken_clue: str = ""
    """Same words as ``clue``, but with ElevenLabs v4 Audio Tags (e.g.,
    "[stress on next word]") inserted by _annotate_for_speech() -- fed to
    text-to-speech ONLY, never shown on screen or to Gemini's judge.
    Empty until that annotation step runs (e.g., under --skip-audio)."""


def _clean(text: str) -> str:
    """Strip a stray-backslash artifact baked into the upstream TSV itself
    -- it backslash-escapes a quote or apostrophe whenever one lands next
    to the tab delimiter (e.g., 'Time Of The Season\\"' or 'singin\\''),
    even outside any quoted CSV field. Real clue text never legitimately
    contains a backslash, so dropping every one is safe (confirmed against
    the real dataset: ~162k backslashes total, 99.99% immediately preceding
    a quote or apostrophe, the rest unrelated single-character typos too
    rare to special-case)."""
    return text.replace("\\", "")


def _ensure_terminal_punctuation(text: str) -> str:
    """The dataset's source data omits terminal punctuation entirely (a
    board-display convention -- clues are meant to be read off a lit-up
    board, not as prose) -- but here the clue is read aloud by a witch and
    shown on screen as a complete sentence, so it needs a proper period
    (or whatever it already ends with) for both TTS pacing and
    readability."""
    return text if text[-1] in ".?!" else f"{text}."


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
"""Marking up clue text with ElevenLabs v4's "Audio Tags" (bracketed
delivery cues like "[stress on next word]") for livelier, better-paced
TTS -- see trivia_game.py's module docstring for why trivia needs live
Gemini access at all; this use of it stays entirely in one-time prep,
never in a live round. Its own client/model, same reasoning as
trivia_game.py's _GEMINI_CLIENT -- these sibling modules stay free of a
dependency on each other."""
_GEMINI_MODEL = "gemini-3.8-flash"
_TAG_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.2,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
"""temperature favors faithful tag insertion over creative rewriting."""
_TAG_PATTERN = re.compile(r"\[[^\]]*\]")


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
witch hosting a trivia game. Do not change, add, remove, or reorder a
single word of the actual text -- only insert bracketed tags between words.

Text: {clue_text!r}

Return ONLY a valid JSON object in this exact format, with no extra text:
{{"spoken": "...with tags inserted..."}}
"""


async def _annotate_for_speech(clue: "Clue") -> "Clue":
    """Ask Gemini to mark up ``clue.clue`` with Audio Tags for TTS, with no
    change to the actual wording -- the clue shown on screen and the one
    handed to Gemini for judging both keep reading ``clue.clue`` untouched;
    only the spoken audio ever sees the tagged version.

    Falls back to the untagged text (rather than risking corrupted trivia
    content) if Gemini's rewrite doesn't survive a strip-the-tags-and-
    compare check -- accuracy matters more than the emphasis polish.
    """
    response = await _GEMINI_CLIENT.aio.models.generate_content(
        model=_GEMINI_MODEL,
        contents=_annotation_prompt(clue.clue),
        config=_TAG_CONFIG,
    )
    if response.text is None:
        return replace(clue, spoken_clue=clue.clue)
    # Same convention as spell_generator._generate_json() -- lets a bad
    # rewrite (or the fidelity-check fallback below) be diagnosed from the
    # log instead of just silently reverting to untagged text.
    print(f"[gemini:annotate] {response.text}", file=sys.stderr)
    try:
        spoken = str(json.loads(response.text)["spoken"])
    except (json.JSONDecodeError, KeyError):
        return replace(clue, spoken_clue=clue.clue)
    if _normalized(_strip_tags(spoken)) != _normalized(clue.clue):
        print(f"Audio Tag rewrite changed clue {clue.id}'s wording -- skipped.")
        return replace(clue, spoken_clue=clue.clue)
    return replace(clue, spoken_clue=spoken)


def _load_cached_spoken(path: Path) -> dict[str, str]:
    """clue_id -> spoken_clue, as already written by a previous run -- lets
    re-running this script reuse prior Audio Tag annotations instead of
    re-querying Gemini for clues whose text hasn't changed, same spirit as
    ensure_voice_cache() skipping already-synthesized audio."""
    if not path.exists():
        return {}
    try:
        rows = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return {row["id"]: row["spoken_clue"] for row in rows if row.get("spoken_clue")}


async def _annotate_clues_for_speech(clues: list["Clue"]) -> list["Clue"]:
    """Sequential, not gathered -- same reasoning as ensure_voice_cache()'s
    own sequential TTS loop."""
    cached = _load_cached_spoken(OUTPUT_PATH)
    annotated = []
    queried = 0
    for clue in clues:
        if clue.id in cached:
            annotated.append(replace(clue, spoken_clue=cached[clue.id]))
        else:
            queried += 1
            annotated.append(await _annotate_for_speech(clue))
    print(
        f"Marked up {queried} new clue(s) with Audio Tags for speech "
        f"({len(clues) - queried} reused from a previous run)."
    )
    return annotated


def _clue_id(category: str, clue: str) -> str:
    """A stable id derived from content, not row position -- re-running the
    filter (e.g., after tweaking the keyword lists) doesn't reshuffle ids
    out from under an already-reviewed excluded_ids.json."""
    digest = hashlib.sha256(f"{category}\x1f{clue}".encode()).hexdigest()
    return digest[:12]


def download_dataset() -> None:
    """Fetch the dataset zip if not already on disk. ~57MB, one-time."""
    if RAW_ZIP_PATH.exists():
        return
    RAW_ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {DATASET_URL} ...")
    urllib.request.urlretrieve(DATASET_URL, RAW_ZIP_PATH)


def extract_tsv() -> None:
    """Pull just the combined-seasons TSV out of the zip -- the release
    also bundles per-season files and a kids/teen-tournament file this
    game has no use for."""
    if TSV_PATH.exists():
        return
    with zipfile.ZipFile(RAW_ZIP_PATH) as archive:
        with archive.open(TSV_MEMBER) as member:
            TSV_PATH.write_bytes(member.read())


def _load_excluded_ids() -> set[str]:
    if not EXCLUDED_IDS_PATH.exists():
        return set()
    return set(json.loads(EXCLUDED_IDS_PATH.read_text()))


def filter_clues() -> list[Clue]:
    """Read the raw TSV and return the horror/Halloween-adjacent subset,
    minus anything hand-excluded via excluded_ids.json."""
    excluded = _load_excluded_ids()
    clues: list[Clue] = []
    with open(TSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            points = POINTS_BY_ROUND.get(row["round"])
            if points is None:
                # Final Jeopardy! (round 3), or a malformed row.
                continue
            category = _clean(row["category"].strip())
            included = _INCLUDE_PATTERN.search(category) and (
                not _EXCLUDE_PATTERN.search(category)
            )
            if not included:
                continue
            clue_text = _clean(row["answer"].strip())
            response = _clean(row["question"].strip())
            if (not clue_text) or (not response):
                continue
            clue_text = _ensure_terminal_punctuation(clue_text)
            clue_id = _clue_id(category, clue_text)
            if clue_id in excluded:
                continue
            clues.append(
                Clue(clue_id, category, clue_text, response, points, int(row["round"]))
            )
    return clues


def _even_count(n: int) -> int:
    """Round down to the nearest even number."""
    return n - (n % 2)


def select_clues(pool: list[Clue], count: int, batch: int) -> list[Clue]:
    """Sample down to ``count`` clues, split evenly between round 1 and
    round 2 (and each half forced even too), then assign each one a
    single witch to voice it.

    Turns alternate strictly between the two teams (see
    trivia_game.submit_answer()), continuing across the round-1/round-2
    boundary rather than resetting there -- but each round carries its
    own fixed point value, so an odd clue count within just one round
    still gives one team an extra primary attempt specifically at that
    round's point value. The deficit only evens out in raw turn count,
    not in which point value the extra turn landed on. Splitting into
    two independently-even halves removes that asymmetry entirely,
    rather than only guaranteeing an even grand total.

    SAMPLE_SEED/WITCH_SEED are fixed, not fresh randomness each run, so
    re-running this script for the *same* batch (e.g., after adding
    something to excluded_ids.json) doesn't reshuffle the selection and
    throw away already-synthesized audio. ``batch`` picks which
    non-overlapping slice of that one fixed shuffle to use -- each round's
    pool is shuffled once under the fixed seed, then cut into consecutive
    ``count``-sized chunks, so batch 0, 1, 2, ... never share a clue with
    each other, while re-running the same batch number always reproduces
    the exact same clues.
    """
    total = _even_count(count)
    half = total // 2
    round_targets = (half, half) if half % 2 == 0 else (half - 1, half + 1)

    sample_rng = random.Random(SAMPLE_SEED)
    sampled: list[Clue] = []
    for round_num, target in zip((1, 2), round_targets):
        round_pool = sorted(
            (clue for clue in pool if clue.round == round_num), key=lambda c: c.id
        )
        sample_rng.shuffle(round_pool)
        actual = _even_count(min(target, len(round_pool)))
        start = batch * actual
        chunk = round_pool[start : start + actual]
        if len(chunk) < actual:
            raise ValueError(
                f"Round {round_num} doesn't have enough unused clues left for "
                f"batch {batch} (needed {actual}, only {len(chunk)} left after "
                f"the {start} already claimed by earlier batches) -- lower "
                "--count, pick an earlier --batch, or widen the keyword filter."
            )
        sampled.extend(chunk)

    witch_rng = random.Random(WITCH_SEED)
    return [replace(clue, witch=witch_rng.choice(list(Witch))) for clue in sampled]


def _clue_audio_path(witch: Witch, clue_id: str) -> Path:
    return AUDIO_CACHE_DIR / f"{witch}_{clue_id}.mp3"


async def ensure_voice_cache(clues: list[Clue]) -> None:
    """Synthesize any missing clue audio -- one witch per clue, whichever
    select_clues() assigned it, not all three; this is a one-night event,
    not a standing feature, so there's no real benefit to voicing every
    clue three times over for variety nobody will notice in one evening.
    See this module's docstring for why this runs here and not live.

    Sequential, not gathered: ElevenLabs' concurrent-request limit (even
    on paid tiers) rejects a burst of concurrent requests. At CLUE_COUNT's
    default size this finishes quickly; it only gets slow if --count is
    pushed back up toward the full pool.
    """
    missing = [
        clue
        for clue in clues
        if not _clue_audio_path(Witch(clue.witch), clue.id).exists()
    ]
    if not missing:
        print("Trivia voice cache already complete.")
        return
    print(f"Synthesizing {len(missing)} trivia clue line(s).")
    for clue in missing:
        witch = Witch(clue.witch)
        spoken = clue.spoken_clue or clue.clue
        text = f"From {clue.category}, for {clue.points} point{'s' if clue.points != 1 else ''}: {spoken}"
        await generate_to_path(text, VOICES[witch], _clue_audio_path(witch, clue.id))
    print("Trivia voice cache ready.")


async def _prepare_audio(clues: list[Clue]) -> list[Clue]:
    """Mark up every clue for speech, then synthesize whatever's missing --
    one event loop for both steps rather than two."""
    clues = await _annotate_clues_for_speech(clues)
    await ensure_voice_cache(clues)
    return clues


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download, filter, and voice-cache horror/Halloween "
        "trivia clues from a public Jeopardy! clue dataset."
    )
    parser.add_argument(
        "--skip-audio",
        action="store_true",
        help="Only (re)write trivia/horror_clues.json; skip ElevenLabs "
        "synthesis. Useful while tuning the keyword filter.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=CLUE_COUNT,
        help=f"How many clues to sample for the night (default: {CLUE_COUNT}; "
        "the full filtered pool is usually well over a thousand).",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=0,
        help="Which non-overlapping batch of clues to select (0-indexed, "
        "default: 0). Batches never share a clue with each other, so "
        "bumping this is how you get an entirely new set of questions for "
        "another night; re-running the same --batch always reproduces the "
        "same clues.",
    )
    args = parser.parse_args()

    download_dataset()
    extract_tsv()
    pool = filter_clues()
    clues = select_clues(pool, args.count, args.batch)

    if not args.skip_audio:
        clues = asyncio.run(_prepare_audio(clues))

    OUTPUT_PATH.write_text(json.dumps([asdict(c) for c in clues], indent=2) + "\n")
    print(
        f"{len(clues)} clues (sampled from {len(pool)} candidates) written to {OUTPUT_PATH}."
    )


if __name__ == "__main__":
    main()
