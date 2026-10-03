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
    {{"prompt": "the request text or riddle",
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


def joke(*, order: str) -> str:
    """Joke mode: a fun, spooky joke about whatever is in the tray photo."""
    return f"""
You are {_WITCHES}, trading a joke around the cauldron.

Analyze the image of the items dropped in the cauldron's capture tray.
Tell a short, fun, spooky joke that works the identified objects into its
setup or punchline. Split it into three parts, one per witch, each picking
up right where the last left off so it reads as one joke rather than three
separate ones -- a setup, a middle beat, and a punchline is the natural
split, but let the joke's own shape decide. This does not need to rhyme --
write it as spoken, comedic delivery instead.
{_order_note(order)}
The last line spoken should land the punchline.

{_reply(_SPELL_SHAPE)}
"""


def prophecy(*, order: str) -> str:
    """Prophecy mode: an over-dramatic fortune about the mortal's future."""
    return f"""
You are {_WITCHES}, peering into the cauldron to read a mortal's fortune.

Analyze the image of the items dropped in the cauldron's capture tray.
Deliver a campy, over-dramatic prophecy about the mortal's near future,
weaving the identified objects into omens and portents -- treat each object
as a sign to be theatrically interpreted, not just named. Write it in three
parts, one per witch, each building on the last so it reads as one unified
prophecy rather than three separate readings. This does not need to rhyme --
lean into tarot-reading theatricality: ominous but ultimately playful, not
actually frightening.
{_order_note(order)}
The last line spoken should deliver the prophecy's final, dramatic verdict.

{_reply(_SPELL_SHAPE)}
"""


def request(items: tuple[str, ...], *, order: str) -> str:
    """Request mode: phrase requests and write witch lines for given objects.

    ``items`` are the exact objects to ask for, chosen ahead of time from a
    curated pool rather than invented here -- the model's job is purely the
    phrasing (style and wording), not picking what to ask for.
    """
    items_text = "\n".join(f'- "{item}"' for item in items)
    return f"""
You are {_WITCHES}, deciding how to ask a mortal to fetch these exact objects:
{items_text}

Give each object a "style":
  - "explicit": the request names the object plainly.
  - "riddle": the request is a short riddle whose answer is the object; the
    object itself is never named.
Use a mix when there is more than one object.
List the ingredients in the same order given above.

Then write three short rhyming lines, one per witch, that together ask the
mortal to bring all {len(items)} objects. Riddled objects stay riddles in the lines.
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
    order: str,
) -> str:
    """Request mode: the witches' one-shot reaction spell, keyed to the verdict."""
    return f"""
You are {_WITCHES}, reacting to what the mortal brought.

Requested: {requested}
Brought:   {found}
Missing:   {missing}
Extras:    {extras}
Verdict:   {verdict}

Write a short three-line rhyming spell, one line per witch, reacting to the
verdict. On SUCCESS be triumphant and let the potion work. On PARTIAL the brew
is weak or half-formed. On FAILURE mock the mortal and let the spell backfire
comically. The mortal gets one chance, win or lose, so make it conclusive
either way.
{_order_note(order)}

{_reply(_SPELL_SHAPE)}
"""

