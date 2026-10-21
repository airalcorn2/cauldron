"""Horror-trivia game: two teams answer phone-submitted free text, alternating
turns, with a steal chance when the active team misses.

Reads `trivia/horror_clues.json` and the pre-synthesized clue audio under
`generated/trivia/` -- see trivia_data.py, which must be run once ahead of
time for those. Judging each answer, though, calls Gemini live during the
round (see judge_answer()) -- unlike the rest of this project's mini-games,
this one needs real network access while actually playing, not just to
prepare beforehand.

Rules:
  - Clues alternate strictly between the two teams -- whichever team held
    the primary attempt this round, the OTHER team holds it next round,
    regardless of who (if anyone) scored. Steal is a bonus scoring chance
    layered on top of that alternation, not something that changes it.
  - A team's primary attempt right/wrong is decided by judge_answer() --
    Gemini grades the free-text answer and writes the judging witch's
    in-character reaction to it in the same call, not a host judgment
    call and not a local heuristic.
  - Primary wrong (or timed out) -> the other team gets one steal attempt
    at the same clue before the next (alternating) clue is dealt.
  - A tied score once the pool runs out goes to sudden death: a single
    numeric question (see TIEBREAKER_QUESTIONS) that both teams answer at
    once, not alternating turns -- whichever guess lands closer to the
    real, sourced answer wins outright.

Phones join and answer over trivia_web.py's WebSocket; this module owns
the rules, trivia_web.py owns the HTTP/WebSocket plumbing. Both run as
coroutines on one asyncio event loop -- blocking calls (audio.play_file(),
the light_control effects) are pushed onto the loop's executor so they
never block the web server; judge_answer() and voice synthesis use their
SDKs' own async clients instead.

Run directly to host a round against real hardware:

    .venv/bin/python trivia_game.py
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import random
import re
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types

import audio
import light_control
import trivia_web
from trivia_data import AUDIO_CACHE_DIR, OUTPUT_PATH, Clue
from trivia_tiebreakers import TIEBREAKER_QUESTIONS, NumericTiebreaker
from voice_generator import VOICES, generate_to_path
from witches import WITCHES, Witch

TeamId = Literal[1, 2]
Phase = Literal["primary", "steal"]

GAME_TITLE = "The Cauldron of Knowledge"

# 1 is pumpkin orange, 2 is Broomstick purple.
TEAM_COLORS: dict[TeamId, light_control.RGB] = {
    1: light_control.RGB(255, 100, 0),
    2: light_control.RGB(150, 30, 220),
}
TEAM_NAMES: dict[TeamId, str] = {
    1: "Team Tarot",
    2: "Team Broomstick",
}

# Untimed trivia with a live crowd stalls; a timeout is treated exactly
# like a wrong (empty) answer (see judge_answer()).
ANSWER_TIMEOUT_SEC = 120.0
# How long before the deadline the reading witch calls out a warning (see
# WARNING_LINES) -- must stay comfortably under ANSWER_TIMEOUT_SEC.
WARNING_LEAD_SEC = 10.0
# Subtracted on top of WARNING_LEAD_SEC when scheduling the warning (see
# _speak_warning()) -- play_file() still has to spawn mpg123/aplay and open
# the ALSA device before anything's actually audible, even with the file
# already cached, so firing right at the WARNING_LEAD_SEC mark lands the
# audible warning a little after it. Found by feel, not measured exactly.
WARNING_PLAYBACK_LEAD_SEC = 1.0

# Judging and banter are one Gemini call -- see judge_answer(). Mirrors
# spell_generator.py's client setup (retry on the vision model's occasional
# 503s, timeout per attempt rather than hanging the round) but kept as its
# own client/model here rather than imported from spell_generator, so
# trivia's modules stay free of any dependency on the main show (same
# reasoning as duplicating button indices instead of importing
# cauldron_controller -- see _FACE_COLORS in potion_play.py, for instance).
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
_GEMINI_MODEL = "gemini-3.8-flash"
# temperature leaves room for banter variety; the judging half of the
# prompt is explicit enough not to need a cooler temperature too.
_JUDGE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.9,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)

# Overwritten fresh for every judged answer -- unlike clue audio, there's
# nothing to cache here since the text is different every time.
_BANTER_AUDIO_PATH = AUDIO_CACHE_DIR / "_banter.mp3"

# A steal reopens the SAME clue for the other team -- re-reading the whole
# question again would be tedious and sounds like a mistake, so the witch
# announces the steal chance instead (the clue/category stay up on the
# group screen throughout, so nothing's actually lost).
#
# Hand-written (not Gemini-generated), so ElevenLabs v4 Audio Tags are just
# inserted directly -- no fidelity check needed like judge_answer()'s, since
# we're the ones who wrote the words. The only variable is the team name,
# which is always one of exactly two values, so every (witch, team, line)
# combination is pre-synthesized once by ensure_line_cache() rather than
# calling ElevenLabs live for these moments -- see that function.
STEAL_LINES = (
    "[dramatic pause] {team}, the cauldron bubbles with a second chance -- "
    "[excited] steal it!",
    "[ominous] A missed spell leaves an opening... [excited] {team}, it's "
    "yours to steal!",
    "[dramatic pause] The other cauldron grows cold. [stress on next word] "
    "{team}, seize your chance to steal!",
    "[excited] Fortune turns! [stress on next word] {team}, the steal is "
    "yours for the taking.",
)

# Called out by the same witch who read the clue, WARNING_LEAD_SEC before
# the answer clock runs out -- same hand-written/pre-cached reasoning as
# STEAL_LINES above.
WARNING_LINES = (
    "[urgent] Ten seconds, {team} -- the cauldron grows impatient!",
    "[dramatic pause] Tick, tock... [urgent] ten seconds left, {team}!",
    "[stress on next word] Hurry, {team} -- only ten seconds remain!",
)


def _steal_audio_path(witch: Witch, team: TeamId, index: int) -> Path:
    return AUDIO_CACHE_DIR / f"_steal_{witch}_{team}_{index}.mp3"


def _warning_audio_path(witch: Witch, team: TeamId, index: int) -> Path:
    return AUDIO_CACHE_DIR / f"_warning_{witch}_{team}_{index}.mp3"


def _tiebreaker_audio_path(witch: Witch, index: int) -> Path:
    return AUDIO_CACHE_DIR / f"_tiebreaker_{witch}_{index}.mp3"


async def ensure_line_cache() -> None:
    """Pre-synthesize every (witch, team, line) combination of
    STEAL_LINES/WARNING_LINES, plus one reading per witch of each
    TIEBREAKER_QUESTIONS entry, if not already cached. Safe to call every
    time a round starts -- a no-op once the full cross product exists.

    Sequential, not gathered -- same reasoning as
    trivia_data.ensure_voice_cache(): ElevenLabs' concurrent-request limit
    rejects a burst.
    """
    missing: list[tuple[Path, str, Witch]] = []
    for witch in Witch:
        for team in (1, 2):
            for index, line in enumerate(STEAL_LINES):
                path = _steal_audio_path(witch, team, index)
                if not path.exists():
                    missing.append((path, line.format(team=TEAM_NAMES[team]), witch))
            for index, line in enumerate(WARNING_LINES):
                path = _warning_audio_path(witch, team, index)
                if not path.exists():
                    missing.append((path, line.format(team=TEAM_NAMES[team]), witch))
        for index, tiebreaker in enumerate(TIEBREAKER_QUESTIONS):
            path = _tiebreaker_audio_path(witch, index)
            if not path.exists():
                missing.append((path, tiebreaker.question, witch))
    if not missing:
        return
    print(f"Pre-synthesizing {len(missing)} steal/warning/tiebreaker line(s) (one-time).")
    for path, text, witch in missing:
        await generate_to_path(text, VOICES[witch], path)


# Checking a Gemini-written Audio Tag rewrite actually kept the same words
# (see judge_answer()) -- duplicated from trivia_data.py's identical check
# rather than cross-imported, same reasoning as duplicating button indices
# instead of importing cauldron_controller elsewhere in this project: these
# sibling trivia modules stay free of a dependency on each other.
_TAG_PATTERN = re.compile(r"\[[^\]]*\]")


def _strip_tags(text: str) -> str:
    return _TAG_PATTERN.sub("", text)


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True, slots=True)
class Verdict:
    """Gemini's ruling on one attempt, plus the judging witch's
    in-character reaction to it -- see judge_answer()."""

    correct: bool
    # Clean text -- shown on screen, never contains Audio Tags.
    banter: str
    # Same words as ``banter``, but with ElevenLabs v4 Audio Tags inserted
    # for delivery -- fed to text-to-speech only, never displayed.
    spoken_banter: str


def _judge_prompt(
    *,
    witch: Witch,
    category: str,
    clue: str,
    correct_response: str,
    submitted: str | None,
    phase: Phase,
) -> str:
    persona = WITCHES[witch]
    # A primary miss reopens this exact clue as a steal for the other
    # team (see submit_answer()) -- so if this IS a primary attempt,
    # the banter must not give the answer away in case it's wrong,
    # since the other team is about to get a crack at the same clue.
    # Anything else (a correct primary, or a steal either way) is the
    # last word on this clue, so revealing it is safe there.
    secrecy_note = (
        "This is the team's first (primary) attempt at this clue. If "
        "correct, no one else answers this clue, so your banter may "
        "freely state the correct answer. If incorrect, the OTHER team "
        "gets one steal attempt at this exact clue immediately after --"
        "in that case your banter must NOT reveal, confirm, hint at, or "
        "restate the correct answer; only react to how wrong their guess "
        "was, without giving anything away."
        if phase == "primary"
        else "This is a steal attempt (or otherwise the last word on this "
        "clue either way) -- no one else will get to answer it, so your "
        "banter may freely state or confirm the correct answer."
    )
    # A timeout (no submission at all) is a distinct case from a wrong
    # guess -- there's no actual answer to name-check, so the prompt must
    # say that plainly rather than feeding the judge a fake placeholder
    # answer like "no answer given", which it would otherwise quote back
    # verbatim as if the mortals had said those words.
    if submitted is None:
        answer_block = (
            "The mortals gave no answer at all before time ran out -- " "dead silence."
        )
        banter_instruction = (
            "Since they gave no answer, do not invent or quote a fake "
            'answer for them and do not say anything like "no answer '
            'given" -- mock their silence, hesitation, or stalling itself, '
            "in character."
        )
    else:
        answer_block = f"Mortal's answer: {submitted!r}"
        banter_instruction = (
            "Name-check the mortal's actual answer directly, don't just "
            "react generically."
        )
    return f"""
You are {persona.name}, {persona.personality}, one of three eerie,
theatrical Halloween witches hosting a trivia game from a magic cauldron.

Category: {category}
Clue read aloud: {clue}
Correct answer: {correct_response}
{answer_block}

Judge whether the mortal's answer is correct. Be lenient about minor
misspellings, missing articles, or a different phrasing of the same thing,
but it must actually be the same answer -- a vague, wrong, off-topic, or
missing answer is incorrect.

Then write ONE short line of in-character banter as {persona.name},
reacting to this specific exchange -- {banter_instruction} If correct,
praise them. If incorrect, deliver a playful, theatrical roast -- never
genuinely mean, this is a party game. Avoid em dashes or en dashes; use
commas or periods instead, since text-to-speech reads dashes awkwardly.
{secrecy_note}

Also write a second version of that exact same banter line for
text-to-speech: identical wording, but with ElevenLabs "Audio Tags"
inserted -- bracketed delivery directions like [excited], [cackles],
[stress on next word], [dramatic pause] -- at whatever points would make
it land better spoken aloud. Do not change, add, remove, or reorder a
single word; only insert bracketed tags between words.

Return ONLY a valid JSON object in this exact format, with no extra text:
{{"correct": true or false, "banter": "...", "spoken_banter": "...with tags..."}}
"""


async def judge_answer(
    witch: Witch,
    category: str,
    clue: str,
    correct_response: str,
    submitted: str | None,
    phase: Phase,
) -> Verdict:
    """Ask Gemini to grade ``submitted`` against ``correct_response`` and
    write the judging witch's in-character reaction to it, in one call.

    Replaces a local fuzzy-text matcher, which could only ever react
    generically ("correct"/"incorrect"), with a judge that can reason
    about phrasing and respond to what was specifically said. This is the
    one place trivia_game.py touches the network live -- see the module
    docstring. ``phase`` controls whether the banter is allowed to give
    away the correct answer -- see _judge_prompt()'s secrecy_note.
    """
    prompt = _judge_prompt(
        witch=witch,
        category=category,
        clue=clue,
        correct_response=correct_response,
        submitted=submitted,
        phase=phase,
    )
    response = await _GEMINI_CLIENT.aio.models.generate_content(
        model=_GEMINI_MODEL, contents=prompt, config=_JUDGE_CONFIG
    )
    if response.text is None:
        raise ValueError("Gemini returned no text for trivia judging")
    # Same convention as spell_generator._generate_json() -- the raw reply
    # logged to stderr is the only record of a judgment call after the
    # fact, and this is the one spot in the project grading genuinely
    # unpredictable free-text input.
    print(f"[gemini:judge] {response.text}", file=sys.stderr)
    raw = json.loads(response.text)
    banter = str(raw["banter"])
    spoken_banter = str(raw.get("spoken_banter", banter))
    # Same fidelity guard as trivia_data._annotate_for_speech() -- if the
    # tagged rewrite doesn't match the clean banter once tags are stripped,
    # fall back to speaking the clean line untagged rather than risk it
    # having silently changed what was actually said.
    if _normalized(_strip_tags(spoken_banter)) != _normalized(banter):
        spoken_banter = banter
    return Verdict(
        correct=bool(raw["correct"]), banter=banter, spoken_banter=spoken_banter
    )


async def _speak_warning(
    loop: asyncio.AbstractEventLoop, witch: Witch, answerer: TeamId
) -> None:
    """Wait until WARNING_LEAD_SEC is left on the answer clock, then have
    the reading witch call out one of WARNING_LINES (pre-cached, see
    ensure_line_cache() -- no live synthesis to account for here, unlike
    the playback startup latency WARNING_PLAYBACK_LEAD_SEC compensates
    for). Runs as a separate task alongside the answer wait in run(),
    which cancels it early if a response (or the full timeout) arrives
    first -- no point warning after the clock's already stopped mattering.
    """
    await asyncio.sleep(
        max(0.0, ANSWER_TIMEOUT_SEC - WARNING_LEAD_SEC - WARNING_PLAYBACK_LEAD_SEC)
    )
    index = random.randrange(len(WARNING_LINES))
    await loop.run_in_executor(
        None,
        audio.play_file,
        _warning_audio_path(witch, answerer, index),
        True,
        audio.WITCH_VOLUMES[witch],
    )


def other_team(team: TeamId) -> TeamId:
    return 2 if team == 1 else 1


_NUMBER_PATTERN = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_number(text: str) -> float | None:
    """Pull the first plain number out of free text, or None if it
    doesn't contain one -- a tiebreaker guess can be phrased any which
    way ("about 19", "I'd say 19 people"), not just a bare digit string.
    Thousands commas are stripped before parsing; anything else
    (fractions, number words) isn't handled."""
    match = _NUMBER_PATTERN.search(text)
    if match is None:
        return None
    return float(match.group().replace(",", ""))


def closest_team(
    guess_1: float | None, guess_2: float | None, answer: float
) -> TeamId | None:
    """Whichever guess lands nearer ``answer`` wins outright. None if
    neither guess parsed, or if both land exactly as close -- the caller
    then needs a genuine coin flip (see _play_tiebreaker())."""
    if (guess_1 is None) and (guess_2 is None):
        return None
    if guess_1 is None:
        return 2
    if guess_2 is None:
        return 1
    distance_1 = abs(guess_1 - answer)
    distance_2 = abs(guess_2 - answer)
    if distance_1 == distance_2:
        return None
    return 1 if distance_1 < distance_2 else 2


@dataclass
class TriviaState:
    """The round's full state. Mutated via submit_answer()/start_clue(),
    which return a new instance each time -- same immutable-snapshot
    style as brew_game.PotionState, so the turn/steal logic stays a pure,
    directly testable function."""

    team_scores: dict[TeamId, int] = field(default_factory=lambda: {1: 0, 2: 0})
    # Who holds the *primary* attempt this round.
    current_team: TeamId = 1
    phase: Phase = "primary"
    active_clue: Clue | None = None
    remaining: list[Clue] = field(default_factory=list)
    asked: list[Clue] = field(default_factory=list)


def current_answerer(state: TriviaState) -> TeamId:
    """Which team is allowed to submit right now: current_team during the
    primary attempt, the other team during a steal."""
    return (
        state.current_team
        if state.phase == "primary"
        else other_team(state.current_team)
    )


def start_clue(state: TriviaState) -> TriviaState:
    """Deal the next clue as a fresh primary attempt. No-op (returns
    ``state`` unchanged) once the pool is exhausted -- the caller checks
    for that to end the round."""
    if not state.remaining:
        return state
    clue, *rest = state.remaining
    return dataclasses.replace(state, active_clue=clue, remaining=rest, phase="primary")


def submit_answer(state: TriviaState, correct: bool) -> TriviaState:
    """Resolve one attempt (by whichever team current_answerer() says is
    up) at the active clue.

    Primary wrong -> reopens the same clue as a steal for the other team
    (current_team/active_clue/scores untouched; only ``phase`` flips).
    Anything else resolves the round: credit the scorer if ``correct``,
    then alternate ``current_team`` to the other team for next round,
    regardless of who (if anyone) just scored -- strict ABAB turn order,
    steal is a bonus on top of it, not a change to it.
    """
    assert state.active_clue is not None
    if (state.phase == "primary") and (not correct):
        return dataclasses.replace(state, phase="steal")

    scores = state.team_scores
    if correct:
        scorer = current_answerer(state)
        scores = {**scores, scorer: scores[scorer] + state.active_clue.points}
    return dataclasses.replace(
        state,
        team_scores=scores,
        asked=[*state.asked, state.active_clue],
        active_clue=None,
        current_team=other_team(state.current_team),
        phase="primary",
    )


def _clue_audio_path(witch: Witch, clue_id: str) -> Path:
    return AUDIO_CACHE_DIR / f"{witch}_{clue_id}.mp3"


def load_clues() -> list[Clue]:
    """Read trivia_data.py's prepared output. Raises FileNotFoundError
    with a clear message if trivia_data.py hasn't been run yet."""
    if not OUTPUT_PATH.exists():
        raise SystemExit(
            f"{OUTPUT_PATH} not found -- run `.venv/bin/python trivia_data.py` "
            "once first to download, filter, and voice-cache the question bank."
        )
    rows = json.loads(OUTPUT_PATH.read_text())
    return [Clue(**row) for row in rows]


def _local_ip() -> str:
    """Best-effort guess at this Pi's LAN-facing IP, for printing a
    clickable/typeable address at startup. Opens a UDP socket "connected"
    to a public address purely so the OS reports which local interface it
    would route through -- UDP has no handshake, so no packet actually
    goes anywhere. Falls back to "localhost" if that fails (no network at
    all), which at least keeps the printed URL well-formed.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "localhost"


async def run(*, host: str = "0.0.0.0", port: int = 8080) -> TriviaState:
    """Host one full round: start the phone-facing server, deal clues
    until the pool's exhausted, and announce the result. Returns the
    final state.
    """
    clues = load_clues()
    random.shuffle(clues)
    # Shuffle first, then stable-sort by points: randomizes the order
    # within each round while still ramping the whole round up from the
    # 1-point clues to the 2-point ones, Jeopardy!-board style, rather
    # than mixing point values throughout.
    clues.sort(key=lambda clue: clue.points)
    state = TriviaState(remaining=clues)

    loop = asyncio.get_running_loop()
    server = await trivia_web.start(
        host=host, port=port, team_names=TEAM_NAMES, game_title=GAME_TITLE
    )
    ip = _local_ip()
    print(f"Group screen (put this on the TV): http://{ip}:{port}/")
    print("Team pages (normally reached by scanning a QR code from the group screen):")
    for team, name in TEAM_NAMES.items():
        print(f"  {name}: http://{ip}:{port}/team/{team}")
    # Explicit flush -- stdout isn't a tty when run non-interactively, so
    # these prints are fully buffered rather than flushed per line. Without
    # this, a hang or error in light_control.start_rainbow()/
    # audio.start_bubbling_background() just below (both run before
    # input(), whose own prompt write is what would otherwise flush this)
    # means the group-screen URLs never actually show up.
    sys.stdout.flush()

    # One-time (see ensure_line_cache()) -- near-instant on every run after
    # the first, since it's a no-op once the full steal/warning cache exists.
    await ensure_line_cache()

    # The group screen shows its join/QR intro the instant it loads, but
    # nothing should actually start until the host says so -- otherwise
    # the first clue gets dealt (and its 25s answer clock starts ticking)
    # the moment the server comes up, before anyone's even looked at the
    # TV yet, let alone scanned a QR code and joined a team.
    #
    # Same idle rainbow cauldron_controller.py uses for the main show's own
    # "nothing happening yet" waits -- genuinely idle here too (nobody's
    # joined, no voice line is about to play), so it's safe to leave
    # running for as long as the host takes.
    #
    # Bubbling starts here too, but runs as its own aplay subprocess
    # outside pygame.mixer entirely (see audio.start_bubbling_background())
    # -- unlike the rainbow (scoped to idle windows), it keeps playing for
    # the whole round, including right through every voice line, since
    # play_file() never needs to release it.
    await loop.run_in_executor(None, light_control.start_rainbow)
    await loop.run_in_executor(
        None, audio.start_bubbling_background, audio.IDLE_BUBBLE_VOLUME
    )
    await loop.run_in_executor(
        None,
        input,
        "\nPress Enter once both teams have joined and the group screen is up to start the round...\n",
    )
    await loop.run_in_executor(None, light_control.stop_rainbow)

    try:
        state = start_clue(state)
        while state.active_clue is not None:
            state = await _play_attempt(state, server, loop)
            if state.active_clue is None:
                state = start_clue(state)

        if state.team_scores[1] == state.team_scores[2]:
            question = random.choice(TIEBREAKER_QUESTIONS)
            print(f"Scores tied -- sudden death: {question.question}")
            winner: TeamId = await _play_tiebreaker(server, loop, question)
        else:
            winner = 1 if state.team_scores[1] > state.team_scores[2] else 2
        await server.broadcast_state(state)
        await server.broadcast_end(state, winner)
        await loop.run_in_executor(
            None, light_control.victory_swirl, TEAM_COLORS[winner]
        )
        print(
            f"Trivia round complete. Team 1: {state.team_scores[1]}, "
            f"Team 2: {state.team_scores[2]}. Winner: Team {winner}."
        )
        return state
    finally:
        # Bubbling has been playing continuously since the pre-round wait
        # (see audio.start_bubbling_background()) -- this is the one place
        # it actually stops, now that the round itself is over.
        await loop.run_in_executor(None, audio.stop_bubbling_background)
        await server.stop()


async def _play_attempt(
    state: TriviaState, server: trivia_web.Server, loop: asyncio.AbstractEventLoop
) -> TriviaState:
    """Resolve exactly one attempt (primary or steal) at ``state``'s
    active clue -- reading it, waiting for an answer, judging it, and
    lighting the outcome -- returning the updated state.

    Extracted out of run()'s main loop so the sudden-death tiebreaker
    (also in run()) can drive the same per-attempt logic against its own
    small reserve of clues, rather than duplicating it.
    """
    clue = state.active_clue
    assert clue is not None
    # Assigned once by trivia_data.select_clues().
    witch = Witch(clue.witch)
    # Computed before any broadcast so both broadcast_state()'s
    # highlight and broadcast_prompt()'s "whose turn" agree -- during
    # a steal, this is the OTHER team from state.current_team.
    answerer = current_answerer(state)
    await server.broadcast_state(state, answering_team=answerer)
    # Show the clue on the group screen as soon as the witch starts
    # reading it, not once she's done -- broadcast_prompt() before
    # the (blocking) audio playback, not after.
    await server.broadcast_prompt(answerer, clue, state.phase)
    # Idle rainbow starts here, before the witch even starts
    # talking, and keeps spinning straight through both the
    # reading and the answer wait below -- the room's attention is
    # most on the ring right as a clue begins, so it shouldn't go
    # dark at exactly that moment.
    await loop.run_in_executor(None, light_control.start_rainbow)
    if state.phase == "primary":
        await loop.run_in_executor(
            None,
            audio.play_file,
            _clue_audio_path(witch, clue.id),
            True,
            audio.WITCH_VOLUMES[witch],
        )
    else:
        # Don't re-read the clue -- it's the same question, just
        # reopened for the other team; a short in-character steal
        # announcement instead of the full question again.
        # Pre-cached (see ensure_line_cache()), not synthesized here.
        steal_index = random.randrange(len(STEAL_LINES))
        await loop.run_in_executor(
            None,
            audio.play_file,
            _steal_audio_path(witch, answerer, steal_index),
            True,
            audio.WITCH_VOLUMES[witch],
        )

    # The rainbow (already spinning since before the reading
    # started) keeps going through the team's answer wait, the
    # submission splash, and Gemini's judging below -- stopped
    # only once, right before the outcome lighting takes over.
    #
    # The countdown starts here, not when the prompt was first
    # broadcast -- the clue/steal line's own (variable-length)
    # reading just finished, so this is the actual moment the
    # answer clock begins.
    await server.broadcast_timer(ANSWER_TIMEOUT_SEC)
    warning_task = asyncio.create_task(_speak_warning(loop, witch, answerer))
    try:
        response = await asyncio.wait_for(
            server.wait_for_answer(answerer), timeout=ANSWER_TIMEOUT_SEC
        )
    except asyncio.TimeoutError:
        response = None
    finally:
        # A response (or the full timeout) landed before
        # WARNING_LEAD_SEC was even reached -- no point warning
        # about a clock that's already stopped mattering.
        if not warning_task.done():
            warning_task.cancel()
        try:
            await warning_task
        except asyncio.CancelledError:
            pass

    if response is not None:
        # A submission actually landed (as opposed to the clock
        # running out) -- a quick splash of sound and light marking
        # the moment, distinct from the eventual correct/incorrect
        # outcome below (not known yet -- judge_answer() hasn't
        # run). trigger_splash() composites a burst of brightness
        # pops directly into the still-spinning rainbow (same
        # technique potion_play.py uses for its heat bubbles)
        # rather than taking the ring over -- no stop/restart
        # dance, and no dark gap before the rainbow "resumes" (it
        # never stopped).
        light_control.trigger_splash()
        # No volume arg -- SPLASH_SFX_PATH is now a prerendered
        # .wav (see audio._ensure_splash_audio()) with
        # SPLASH_VOLUME already baked in; aplay has no live
        # volume flag to apply it with anyway.
        await loop.run_in_executor(None, audio.play_file, audio.SPLASH_SFX_PATH)
    # The witch-laugh "something's happening" cue, same one react
    # mode pairs with its own API wait (see
    # cauldron_controller._start_ambience()) -- starts the instant
    # the wait is over either way (after the splash if there was
    # one), filling what would otherwise be dead air through
    # judging below, including on a bare timeout with no splash at
    # all. No flicker takeover here, unlike react mode; the
    # rainbow just keeps spinning alongside it.
    await loop.run_in_executor(None, audio.start_laugh)

    verdict = await judge_answer(
        witch, clue.category, clue.clue, clue.response, response, state.phase
    )

    # Still rainbow-lit and laughing here, deliberately --
    # generate_to_path() below is a real ElevenLabs synthesis call
    # with its own nontrivial wait, and stopping either one before
    # it would cut them off for that whole stretch, not just the
    # instant between judging and the banter actually starting.
    # Both stop right before the outcome's steady color and the
    # banter itself take over instead, once there's actually
    # something else ready to play/paint.
    await generate_to_path(verdict.spoken_banter, VOICES[witch], _BANTER_AUDIO_PATH)
    await loop.run_in_executor(None, audio.stop_laugh)
    await loop.run_in_executor(None, light_control.stop_rainbow)
    speak_banter = loop.run_in_executor(
        None,
        audio.play_file,
        _BANTER_AUDIO_PATH,
        True,
        audio.WITCH_VOLUMES[witch],
    )

    team_up = answerer
    await server.broadcast_result(team_up, verdict.correct, state.phase, verdict.banter)
    # Hold a solid, outcome-appropriate color for the ENTIRE banter
    # line, then run a quick flourish once it's done -- not the
    # other way around. celebrate()/fizzle() are fixed-duration
    # animations (celebrate()'s 6 blinks is ~0.7s, fizzle()'s fade
    # ~1s), too short to gather alongside an arbitrary-length
    # banter line without risking a dark ring for whatever's left
    # once the animation ends. A plain set_leds() call needs no
    # such gather: the strip just holds whatever it was last
    # painted, with no further CPU/SPI involvement, for exactly as
    # long as nothing else repaints it -- i.e., for the full,
    # actual duration of speak_banter, whatever that turns out to
    # be. Mirrors how cauldron_controller.py already uses
    # celebrate() elsewhere: as a flourish AFTER the voice line,
    # not during it.
    if verdict.correct:
        await loop.run_in_executor(None, light_control.set_leds, TEAM_COLORS[team_up])
        await speak_banter
        await loop.run_in_executor(None, light_control.celebrate, TEAM_COLORS[team_up])
    elif state.phase == "steal":
        await loop.run_in_executor(None, light_control.set_leds, light_control.RED)
        await speak_banter
        await loop.run_in_executor(None, light_control.fizzle)
    else:
        await loop.run_in_executor(None, light_control.set_leds, light_control.RED)
        await speak_banter
        await loop.run_in_executor(None, light_control.leds_off)

    return submit_answer(state, verdict.correct)


async def _play_tiebreaker(
    server: trivia_web.Server,
    loop: asyncio.AbstractEventLoop,
    question: NumericTiebreaker,
) -> TeamId:
    """Sudden death: both teams guess the SAME numeric question at once --
    no turns, no steal -- and whichever guess lands closer to the real
    answer (see closest_team()) wins outright. Falls back to an honest
    coin flip only if neither guess parses as a number, or both land
    exactly as close (vanishingly unlikely, but needs a real resolution
    rather than quietly favoring Team 1 -- the bug that prompted this
    whole feature).

    The idle rainbow is restarted for the reading/thinking wait, same as
    _play_attempt(), then stopped right before the result lighting in
    run()'s tail takes over.
    """
    witch = random.choice(list(Witch))
    index = TIEBREAKER_QUESTIONS.index(question)
    await server.broadcast_tiebreaker(question.question)
    await loop.run_in_executor(None, light_control.start_rainbow)
    await loop.run_in_executor(
        None,
        audio.play_file,
        _tiebreaker_audio_path(witch, index),
        True,
        audio.WITCH_VOLUMES[witch],
    )
    await server.broadcast_timer(ANSWER_TIMEOUT_SEC)

    async def _wait(team: TeamId) -> float | None:
        try:
            text = await asyncio.wait_for(
                server.wait_for_answer(team), timeout=ANSWER_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            return None
        return extract_number(text)

    guess_1, guess_2 = await asyncio.gather(_wait(1), _wait(2))
    winner = closest_team(guess_1, guess_2, question.answer)
    if winner is None:
        winner = 1 if random.random() < 0.5 else 2

    await loop.run_in_executor(None, light_control.stop_rainbow)
    await server.broadcast_tiebreaker_result(
        guess_1=guess_1,
        guess_2=guess_2,
        answer=question.answer,
        source=question.source,
        winner=winner,
    )
    return winner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Host one round of horror trivia for two phone-connected teams."
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    light_control.setup()
    audio.setup()
    asyncio.run(run(port=args.port))


if __name__ == "__main__":
    main()
