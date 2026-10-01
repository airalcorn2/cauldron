"""Prompt text for the cauldron's Gemini calls.

Each public function returns a fully rendered prompt string. The wording that
several prompts share -- the witch personas, the "return JSON only" instruction,
and the reply shapes -- is defined once here.
"""

from __future__ import annotations

from witches import WITCHES, Witch

_COUNT_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _join_and(items: list[str]) -> str:
    """Join with commas and "and" before the last item (Oxford comma for 3+)."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


# Built from the WITCHES registry rather than spelled out, so adding or
# editing a witch there is all it takes to update the prompts.
_WITCHES = (
    f"the {_COUNT_WORDS[len(WITCHES)]} eerie, theatrical Halloween witches: "
    + _join_and([f"{p.name} ({p.personality})" for p in WITCHES.values()])
)

_RETURN_JSON = (
    "Return ONLY a valid JSON object in this exact format, with no extra text:"
)

# Built from Witch rather than spelled out, so the reply shapes never drift
# from the actual set of witches.
_SPELL_SHAPE = "{" + ", ".join(f'"{w}": "..."' for w in Witch) + "}"

_RECIPE_SHAPE = f"""{{
  "ingredients": [
    {{"prompt": "the request text or riddle", "answer": "the plain object name",
     "match_terms": ["keyword", "keyword"], "style": "explicit or riddle"}}
  ],
  "lines": {_SPELL_SHAPE}
}}"""

_EVAL_SHAPE = """{
  "present": ["a leaf"],
  "extras": ["a rubber duck"]
}"""


def _reply(shape: str) -> str:
    """The closing instruction: return JSON only, in the given shape."""
    return f"{_RETURN_JSON}\n{shape}"


def _order_note(order: str) -> str:
    """State who speaks when, so the lines are written to flow in that order.

    The reply shapes key lines by witch name, which the model would otherwise
    read as the running order, so this says outright that it is not.
    """
    return (
        f"The witches speak in this order: {order}.\n"
        "Write the lines to read as one spell in that order. The JSON keys "
        "below name each witch; they are not the running order."
    )


def react(*, order: str) -> str:
    """React mode: a spell about whatever is in the tray photo."""
    return f"""
You are {_WITCHES}.

Analyze the image of the items dropped in the cauldron's capture tray.
Generate a short three-line rhyming spell that references the identified
objects, one line per witch.
The spell should have an effect in the world of the cauldron—the witches
are casting it on behalf of the mortal.
{_order_note(order)}
The last line spoken should explain what the spell will do, cleverly
referencing the objects.

{_reply(_SPELL_SHAPE)}
"""


def story(*, order: str) -> str:
    """Story mode: a short spooky story about whatever is in the tray photo."""
    return f"""
You are {_WITCHES}, taking turns telling a story around the cauldron.

Analyze the image of the items dropped in the cauldron's capture tray.
Weave a short, spooky story that features the identified objects as part of
what happens in it -- found, used, hidden, cursed, or transformed, your call.
Write it in three parts, one per witch, each picking up right where the last
left off so it reads as one continuous tale rather than three separate ones.
Unlike a spell, this does not need to rhyme -- write it as atmospheric, eerie
prose instead, two to three sentences per witch.
{_order_note(order)}
End on a spooky, satisfying note.

{_reply(_SPELL_SHAPE)}
"""


def request(count: int, *, inspiration: str, order: str) -> str:
    """Request mode: invent a recipe of objects plus the witch lines.

    ``inspiration`` is a random angle to draw from, there only to keep
    successive rounds from repeating.
    """
    return f"""
You are {_WITCHES}, deciding what a mortal must throw into the cauldron.
Invent {count} small, ordinary objects a trick-or-treater could plausibly find
nearby. For fresh ideas this round, picture rummaging through: {inspiration}.
Do not feel bound to it; just let it pull you away from the obvious.

Give each object a "style":
  - "explicit": the request names the object plainly.
  - "riddle": the request is a short riddle whose answer is the object; the
    object itself is never named.
Use a mix when {count} is more than one.

Then write three short rhyming lines, one per witch, that together ask the
mortal to bring all {count} objects. Riddled objects stay riddles in the lines.
{_order_note(order)}

{_reply(_RECIPE_SHAPE)}
"""


def evaluate(items: str) -> str:
    """Request mode: check a tray photo against the requested items."""
    return f"""
The witches asked the mortal to bring these items:
{items}

Look at the photo of the cauldron's capture tray. For each requested item,
decide whether something matching it is present. Be generous: a rough match
counts. Also list any obvious objects that were not requested.

{_reply(_EVAL_SHAPE)}
"""


def outcome(
    *,
    requested: str,
    found: str,
    missing: str,
    extras: str,
    verdict: str,
    final: bool,
    order: str,
) -> str:
    """Request mode: the witches' reaction spell, keyed to the verdict."""
    return f"""
You are {_WITCHES}, reacting to what the mortal brought.

Requested: {requested}
Brought:   {found}
Missing:   {missing}
Extras:    {extras}
Verdict:   {verdict}
Final attempt: {final}

Write a short three-line rhyming spell, one line per witch, reacting to the
verdict. On SUCCESS be triumphant and let the potion work. On PARTIAL the brew
is weak or half-formed. On FAILURE mock the mortal and let the spell backfire
comically. If this was the final attempt, make it conclusive.
{_order_note(order)}

{_reply(_SPELL_SHAPE)}
"""


def hint(*, missing: str, extras_note: str, order: str) -> str:
    """Request mode: goading lines between retries, nudging toward the misses."""
    return f"""
You are {_WITCHES}, growing impatient. The mortal still owes you:
{missing}
{extras_note}

Write three short taunting lines, one per witch, nudging them toward what is
still missing. If a missing item was a riddle, keep it a riddle and do not name
it. Playful, not cruel.
{_order_note(order)}

{_reply(_SPELL_SHAPE)}
"""
