"""Vision-model spell and recipe generation for the cauldron.

React mode -- spell about whatever is in the tray:

    .venv/bin/python spell_generator.py test.jpg

Story mode -- a short spooky story about whatever is in the tray:

    .venv/bin/python spell_generator.py test.jpg --story

Joke mode -- a fun, spooky joke about whatever is in the tray:

    .venv/bin/python spell_generator.py test.jpg --joke

Prophecy mode -- a campy, over-dramatic fortune about whatever is in the tray:

    .venv/bin/python spell_generator.py test.jpg --prophecy

Request mode helpers:

    .venv/bin/python spell_generator.py --request [--ingredients 2]   # Invent a recipe.
    .venv/bin/python spell_generator.py --evaluate recipe.json tray.jpg
    .venv/bin/python spell_generator.py --outcome  recipe.json tray.jpg
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

import prompts
from witches import Witch

# Vision requests draw occasional 503s when the model is busy, so retry with
# backoff rather than dropping to the pre-recorded spell on the first blip; the
# bubbling SFX and LED strobe cover the wait. The timeout is per attempt, so a
# stalled connection is cut loose and retried instead of hanging the show.
CLIENT = genai.Client(
    http_options=types.HttpOptions(
        timeout=30_000,  # Milliseconds.
        retry_options=types.HttpRetryOptions(
            attempts=6,
            initial_delay=1.0,
            max_delay=15.0,
            exp_base=2.0,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    )
)
MODEL = "gemini-3.8-flash"

# No tools are passed, so AFC is disabled to silence the SDK warning. The
# creative calls (spells, recipes) run hot for variety; tray evaluation runs
# cool for accuracy.
_CREATIVE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=1.4,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)
_PRECISE_CONFIG = types.GenerateContentConfig(
    response_mime_type="application/json",
    temperature=0.3,
    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
)

# Request-mode ingredients are sampled from this curated pool rather than
# invented by the model: it keeps successive rounds from converging on the
# same few objects, and keeps every item sized to actually fit through the
# drop chute (~5" diameter), neither of which the model reliably got right
# on its own. See items.txt.
_ITEMS_PATH = Path("items.txt")
_ITEMS: tuple[str, ...] = tuple(
    line.strip()
    for line in _ITEMS_PATH.read_text().splitlines()
    if line.strip()
)

# Category-mode challenges are sampled from this pool for the same reason
# request-mode ingredients are: keeps successive rounds varied. Each line
# completes "something ___", e.g. "round" -> "something round". See
# categories.txt.
_CATEGORIES_PATH = Path("categories.txt")
_CATEGORIES: tuple[str, ...] = tuple(
    line.strip()
    for line in _CATEGORIES_PATH.read_text().splitlines()
    if line.strip()
)


class RequestStyle(StrEnum):
    """How a requested ingredient is phrased to the mortal."""

    EXPLICIT = "explicit"
    RIDDLE = "riddle"


class Outcome(StrEnum):
    """The verdict on a collection attempt."""

    SUCCESS = "success"  # Every requested ingredient is present.
    PARTIAL = "partial"  # Some present, some missing.
    FAILURE = "failure"  # None of the requested ingredients are present.


@dataclass(frozen=True, slots=True)
class Spell:
    """A complete spell: one line per witch.

    ``order`` is who speaks when. The model is told it up front and writes the
    lines to flow in that sequence, so reciting in any other order breaks the
    rhyme and strands the closing line.
    """

    lines: dict[Witch, str]
    order: tuple[Witch, ...] = tuple(Witch)

    def line(self, witch: Witch) -> str:
        """Return the line spoken by ``witch``."""
        return self.lines[witch]

    def __iter__(self) -> Iterator[tuple[Witch, str]]:
        """Yield ``(witch, line)`` pairs in recitation order."""
        for witch in self.order:
            yield witch, self.line(witch)

    @classmethod
    def from_api_json(cls, payload: str, order: tuple[Witch, ...] = tuple(Witch)) -> Spell:
        """Parse a ``{witch: line, ...}`` JSON object into a Spell."""
        raw = json.loads(payload)
        try:
            lines = {witch: raw[witch.value] for witch in Witch}
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed spell response: {raw!r}") from exc
        return cls(lines=lines, order=order)


@dataclass(frozen=True, slots=True)
class Ingredient:
    """One object the witches want fetched."""

    prompt: str  # What the witches say aloud: a plain request or a riddle.
    answer: str  # The literal object, e.g., "a red leaf".
    match_terms: tuple[str, ...]  # Lowercased keywords for the tray matcher.
    style: RequestStyle

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Ingredient:
        return cls(
            prompt=raw["prompt"],
            answer=raw["answer"],
            match_terms=tuple(term.lower() for term in raw["match_terms"]),
            style=RequestStyle(raw.get("style", RequestStyle.EXPLICIT)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "answer": self.answer,
            "match_terms": list(self.match_terms),
            "style": self.style.value,
        }


@dataclass(frozen=True, slots=True)
class Recipe:
    """A set of requested ingredients plus the witch lines that ask for them."""

    ingredients: tuple[Ingredient, ...]
    lines: dict[Witch, str]
    order: tuple[Witch, ...] = tuple(Witch)

    def announce(self) -> Spell:
        """The lines that ask the mortal for the ingredients."""
        return Spell(lines=self.lines, order=self.order)

    def summary(self) -> str:
        """One-line description for logging."""
        return "; ".join(f"{i.answer} [{i.style}]" for i in self.ingredients)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ingredients": [i.to_dict() for i in self.ingredients],
            "lines": {w.value: self.lines[w] for w in Witch},
            "order": [w.value for w in self.order],
        }

    @classmethod
    def from_api_json(
        cls, payload: str, order: tuple[Witch, ...] | None = None
    ) -> Recipe:
        """Parse a recipe. Raises ``ValueError`` if malformed.

        ``order`` is the speaking order the prompt asked for; a saved recipe
        carries its own, since the lines were written for it.
        """
        raw = json.loads(payload)
        try:
            ingredients = tuple(
                Ingredient.from_dict(item) for item in raw["ingredients"]
            )
            lines = {w: raw["lines"][w.value] for w in Witch}
            if order is None:
                order = tuple(Witch(w) for w in raw.get("order", list(Witch)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed recipe response: {raw!r}") from exc
        if len(ingredients) == 0:
            raise ValueError("recipe has no ingredients")
        return cls(ingredients, lines, order)


@dataclass(frozen=True, slots=True)
class RoundResult:
    """What a tray photo contained, measured against a recipe."""

    recipe: Recipe
    found: frozenset[Ingredient]
    missing: frozenset[Ingredient]
    extras: tuple[str, ...]

    @property
    def outcome(self) -> Outcome:
        if len(self.missing) == 0:
            return Outcome.SUCCESS
        if len(self.found) > 0:
            return Outcome.PARTIAL
        return Outcome.FAILURE


@dataclass(frozen=True, slots=True)
class Challenge:
    """Category mode: a property to satisfy, plus the witch lines that ask for it."""

    category: str  # Completes "something ___", e.g. "round".
    lines: dict[Witch, str]
    order: tuple[Witch, ...] = tuple(Witch)

    def announce(self) -> Spell:
        """The lines that challenge the mortal to bring something matching."""
        return Spell(lines=self.lines, order=self.order)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "lines": {w.value: self.lines[w] for w in Witch},
            "order": [w.value for w in self.order],
        }

    @classmethod
    def from_api_json(cls, payload: str) -> Challenge:
        """Parse a saved challenge. Raises ``ValueError`` if malformed."""
        raw = json.loads(payload)
        try:
            category = raw["category"]
            lines = {w: raw["lines"][w.value] for w in Witch}
            order = tuple(Witch(w) for w in raw.get("order", list(Witch)))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed challenge: {raw!r}") from exc
        return cls(category, lines, order)


@dataclass(frozen=True, slots=True)
class CategoryOutcome:
    """What a tray photo contained, measured against a category challenge."""

    challenge: Challenge
    satisfied: bool
    matching_item: str | None  # Which item satisfied it, if any.
    items_seen: tuple[str, ...]  # Everything visible, for the reaction spell.


def _generate_json(
    contents: object,
    label: str,
    *,
    config: types.GenerateContentConfig = _CREATIVE_CONFIG,
) -> str:
    """Run one JSON generation, log the raw reply to stderr, and return it."""
    response = CLIENT.models.generate_content(
        model=MODEL,
        contents=contents,  # type: ignore[arg-type]
        config=config,
    )
    if response.text is None:
        raise ValueError("The vision model returned no text")
    print(f"[gemini:{label}] {response.text}", file=sys.stderr)
    return response.text


def _random_order() -> tuple[Witch, ...]:
    """A random speaking order, so the same witch does not always open."""
    return tuple(random.sample(list(Witch), len(Witch)))


def _order_text(order: tuple[Witch, ...]) -> str:
    """Render an order for the prompt, e.g. ``Amber, then Hazel, then Violet``."""
    return ", then ".join(witch.value.capitalize() for witch in order)


def _spell_from_prompt(prompt: str, label: str, order: tuple[Witch, ...]) -> Spell:
    """Run a text-only generation and parse the reply as a Spell."""
    return Spell.from_api_json(_generate_json(prompt, label), order)


def generate_spell_from_image(image_path: str | Path) -> Spell:
    """React mode: a spell about the items in a tray photo."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    order = _random_order()
    prompt = prompts.react(order=_order_text(order))
    return Spell.from_api_json(_generate_json([part, prompt], "react"), order)


def generate_story_from_image(image_path: str | Path) -> Spell:
    """Story mode: a short spooky story about the items in a tray photo."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    order = _random_order()
    prompt = prompts.story(order=_order_text(order))
    return Spell.from_api_json(_generate_json([part, prompt], "story"), order)


def generate_joke_from_image(image_path: str | Path) -> Spell:
    """Joke mode: a fun, spooky joke about the items in a tray photo."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    order = _random_order()
    prompt = prompts.joke(order=_order_text(order))
    return Spell.from_api_json(_generate_json([part, prompt], "joke"), order)


def generate_prophecy_from_image(image_path: str | Path) -> Spell:
    """Prophecy mode: a campy, over-dramatic fortune about the tray's items."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    order = _random_order()
    prompt = prompts.prophecy(order=_order_text(order))
    return Spell.from_api_json(_generate_json([part, prompt], "prophecy"), order)


def request_recipe(ingredient_count: int = 2) -> Recipe:
    """Sample objects from the curated pool and ask the model to phrase them.

    The model only decides style (explicit/riddle) and wording, never which
    objects to use -- see _ITEMS.
    """
    order = _random_order()
    items = tuple(random.sample(_ITEMS, ingredient_count))
    prompt = prompts.request(items, order=_order_text(order))
    raw = json.loads(_generate_json(prompt, "recipe"))
    try:
        ingredients = tuple(
            Ingredient.from_dict({**item, "answer": answer})
            for item, answer in zip(raw["ingredients"], items, strict=True)
        )
        lines = {w: raw["lines"][w.value] for w in Witch}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed recipe response: {raw!r}") from exc
    if len(ingredients) == 0:
        raise ValueError("recipe has no ingredients")
    return Recipe(ingredients, lines, order)


def evaluate_tray(recipe: Recipe, image_path: str | Path) -> RoundResult:
    """Check a tray photo against a recipe and report found / missing / extras."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    items = "\n".join(
        f'- "{i.answer}" (keywords: {", ".join(i.match_terms)})'
        for i in recipe.ingredients
    )
    raw = json.loads(
        _generate_json(
            [part, prompts.evaluate(items)], "evaluate", config=_PRECISE_CONFIG
        )
    )

    present = [str(p).lower() for p in raw.get("present", [])]

    def _is_present(ingredient: Ingredient) -> bool:
        answer = ingredient.answer.lower()
        for seen in present:
            if (answer in seen) or (seen in answer):
                return True
            if any(term in seen for term in ingredient.match_terms):
                return True
        return False

    found = frozenset(i for i in recipe.ingredients if _is_present(i))
    missing = frozenset(recipe.ingredients) - found
    extras = tuple(str(x) for x in raw.get("extras", []))
    return RoundResult(recipe=recipe, found=found, missing=missing, extras=extras)


def generate_outcome_spell(result: RoundResult) -> Spell:
    """The witches' one-shot reaction spell, keyed to the verdict."""
    order = _random_order()
    return _spell_from_prompt(
        prompts.outcome(
            requested=", ".join(i.answer for i in result.recipe.ingredients),
            found=(
                ", ".join(i.answer for i in result.found)
                if len(result.found) > 0
                else "nothing"
            ),
            missing=(
                ", ".join(i.answer for i in result.missing)
                if len(result.missing) > 0
                else "nothing"
            ),
            extras=(
                ", ".join(result.extras) if len(result.extras) > 0 else "none"
            ),
            verdict=result.outcome.name,
            order=_order_text(order),
        ),
        "outcome",
        order,
    )


def category_challenge() -> Challenge:
    """Sample a property from the curated pool and ask the model to phrase it.

    The model only decides wording, never which property to challenge with
    -- see _CATEGORIES.
    """
    order = _random_order()
    category = random.choice(_CATEGORIES)
    prompt = prompts.category_challenge(category, order=_order_text(order))
    raw = json.loads(_generate_json(prompt, "challenge"))
    try:
        lines = {w: raw["lines"][w.value] for w in Witch}
    except (KeyError, TypeError) as exc:
        raise ValueError(f"malformed challenge response: {raw!r}") from exc
    return Challenge(category, lines, order)


def evaluate_category(challenge: Challenge, image_path: str | Path) -> CategoryOutcome:
    """Check a tray photo against a category challenge."""
    image_data = Path(image_path).read_bytes()
    part = types.Part.from_bytes(data=image_data, mime_type="image/jpeg")
    raw = json.loads(
        _generate_json(
            [part, prompts.category_evaluate(challenge.category)],
            "category_evaluate",
            config=_PRECISE_CONFIG,
        )
    )
    return CategoryOutcome(
        challenge=challenge,
        satisfied=bool(raw.get("satisfied", False)),
        matching_item=raw.get("matching_item"),
        items_seen=tuple(str(x) for x in raw.get("items_seen", [])),
    )


def generate_category_outcome_spell(result: CategoryOutcome) -> Spell:
    """The witches' one-shot reaction spell, keyed to the category verdict."""
    order = _random_order()
    return _spell_from_prompt(
        prompts.category_outcome(
            category=result.challenge.category,
            items_seen=(
                ", ".join(result.items_seen)
                if len(result.items_seen) > 0
                else "nothing"
            ),
            satisfied=result.satisfied,
            order=_order_text(order),
        ),
        "category_outcome",
        order,
    )


# Pre-written challenges for when category_challenge() cannot reach the API.
FALLBACK_CHALLENGES: tuple[Challenge, ...] = (
    Challenge(
        category="round",
        lines={
            Witch.VIOLET: "Bring us something round, a shape with no end,",
            Witch.AMBER: "no corners to count and no edge to defend!",
            Witch.HAZEL: "Round as the moon, or the cauldron's own rim.",
        },
    ),
    Challenge(
        category="that makes noise",
        lines={
            Witch.VIOLET: "Bring us a sound trapped in something you own,",
            Witch.AMBER: "a rattle, a ring, or a squeak all alone!",
            Witch.HAZEL: "Make this cauldron hear something it's never known.",
        },
    ),
    Challenge(
        category="shiny",
        lines={
            Witch.VIOLET: "Bring us a glimmer, a gleam, or a shine,",
            Witch.AMBER: "something that catches the light just so fine!",
            Witch.HAZEL: "Dull little mortal, go make something mine.",
        },
    ),
)


def random_fallback_challenge() -> Challenge:
    """Pick a pre-written challenge when the API is unavailable."""
    return random.choice(FALLBACK_CHALLENGES)


# Pre-written recipes for when request_recipe() cannot reach the API.
FALLBACK_RECIPES: tuple[Recipe, ...] = (
    Recipe(
        ingredients=(
            Ingredient(
                "a leaf the autumn wind let fall",
                "a leaf",
                ("leaf", "foliage"),
                RequestStyle.EXPLICIT,
            ),
            Ingredient(
                "the cold iron tooth that answers a locked door",
                "a key",
                ("key", "keys"),
                RequestStyle.RIDDLE,
            ),
        ),
        lines={
            Witch.VIOLET: "Bring us a leaf the autumn wind let fall,",
            Witch.AMBER: "and the cold iron tooth that answers a locked hall!",
            Witch.HAZEL: "Two things, no more, or nothing brews at all.",
        },
    ),
    Recipe(
        ingredients=(
            Ingredient(
                "a small coin",
                "a coin",
                ("coin", "penny", "money"),
                RequestStyle.EXPLICIT,
            ),
            Ingredient(
                "a soldier of paper that carries your words away",
                "a stamp",
                ("stamp", "postage"),
                RequestStyle.RIDDLE,
            ),
        ),
        lines={
            Witch.VIOLET: "A coin, round and cold, to weigh down the dark,",
            Witch.AMBER: "and the paper that flies with a lick and a mark!",
            Witch.HAZEL: "Only two. Then watch the embers spark.",
        },
    ),
    Recipe(
        ingredients=(
            Ingredient(
                "a button popped from a coat",
                "a button",
                ("button",),
                RequestStyle.EXPLICIT,
            ),
            Ingredient(
                "the grey little wand that scratches a thought",
                "a pencil",
                ("pencil", "pen"),
                RequestStyle.RIDDLE,
            ),
        ),
        lines={
            Witch.VIOLET: "One button, popped from a coat gone to rot,",
            Witch.AMBER: "and the grey little wand that scratches a thought!",
            Witch.HAZEL: "Bring the pair, or the cauldron gives you naught.",
        },
    ),
)


def random_fallback_recipe() -> Recipe:
    """Pick a pre-written recipe when the API is unavailable."""
    return random.choice(FALLBACK_RECIPES)


def _run_react(image: str) -> None:
    for witch, line in generate_spell_from_image(image):
        print(f"{witch}: {line}")


def _run_story(image: str) -> None:
    for witch, line in generate_story_from_image(image):
        print(f"{witch}: {line}")


def _run_joke(image: str) -> None:
    for witch, line in generate_joke_from_image(image):
        print(f"{witch}: {line}")


def _run_prophecy(image: str) -> None:
    for witch, line in generate_prophecy_from_image(image):
        print(f"{witch}: {line}")


def _run_request(ingredient_count: int) -> None:
    print(json.dumps(request_recipe(ingredient_count).to_dict(), indent=2))


def _run_evaluate(recipe_json: str, image: str, *, with_outcome: bool) -> None:
    recipe = Recipe.from_api_json(Path(recipe_json).read_text())
    result = evaluate_tray(recipe, image)
    print(f"outcome: {result.outcome.name}")
    print(f"found:   {sorted(i.answer for i in result.found)}")
    print(f"missing: {sorted(i.answer for i in result.missing)}")
    print(f"extras:  {list(result.extras)}")
    if with_outcome:
        print("--- outcome spell ---")
        for witch, line in generate_outcome_spell(result):
            print(f"{witch}: {line}")


def _run_challenge() -> None:
    print(json.dumps(category_challenge().to_dict(), indent=2))


def _run_challenge_outcome(challenge_json: str, image: str) -> None:
    challenge = Challenge.from_api_json(Path(challenge_json).read_text())
    result = evaluate_category(challenge, image)
    print(f"satisfied:      {result.satisfied}")
    print(f"matching_item:  {result.matching_item}")
    print(f"items_seen:     {list(result.items_seen)}")
    print("--- outcome spell ---")
    for witch, line in generate_category_outcome_spell(result):
        print(f"{witch}: {line}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a spell (react mode), a story (story mode), a "
        "joke (joke mode), a prophecy (prophecy mode), or a recipe "
        "(request mode)."
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="React/story/joke/prophecy mode: JPEG of the tray to spell/tell/"
        "joke/divine about.",
    )
    flavor = parser.add_mutually_exclusive_group()
    flavor.add_argument(
        "--story",
        action="store_true",
        help="With an image: tell a short spooky story instead of a rhyming "
        "spell (story mode).",
    )
    flavor.add_argument(
        "--joke",
        action="store_true",
        help="With an image: tell a fun, spooky joke instead of a rhyming "
        "spell (joke mode).",
    )
    flavor.add_argument(
        "--prophecy",
        action="store_true",
        help="With an image: deliver a campy, over-dramatic fortune instead "
        "of a rhyming spell (prophecy mode).",
    )
    parser.add_argument(
        "--request",
        action="store_true",
        help="Invent a recipe (objects plus witch lines) and print it as JSON.",
    )
    parser.add_argument(
        "--ingredients",
        type=int,
        default=2,
        help="Ingredient count for --request (default: 2).",
    )
    parser.add_argument(
        "--evaluate",
        nargs=2,
        metavar=("RECIPE_JSON", "IMAGE"),
        help="Check a tray photo against a saved recipe and print the result.",
    )
    parser.add_argument(
        "--outcome",
        nargs=2,
        metavar=("RECIPE_JSON", "IMAGE"),
        help="Like --evaluate, then also generate and print the outcome spell.",
    )
    parser.add_argument(
        "--challenge",
        action="store_true",
        help="Invent a category challenge (property plus witch lines) and "
        "print it as JSON.",
    )
    parser.add_argument(
        "--challenge-outcome",
        nargs=2,
        metavar=("CHALLENGE_JSON", "IMAGE"),
        help="Check a tray photo against a saved challenge and print the "
        "result and outcome spell.",
    )
    args = parser.parse_args()

    if args.request:
        _run_request(args.ingredients)
    elif args.challenge:
        _run_challenge()
    elif args.evaluate is not None:
        _run_evaluate(*args.evaluate, with_outcome=False)
    elif args.outcome is not None:
        _run_evaluate(*args.outcome, with_outcome=True)
    elif args.challenge_outcome is not None:
        _run_challenge_outcome(*args.challenge_outcome)
    elif args.image is not None:
        if args.story:
            _run_story(args.image)
        elif args.joke:
            _run_joke(args.image)
        elif args.prophecy:
            _run_prophecy(args.image)
        else:
            _run_react(args.image)
    else:
        parser.error(
            "give an image, or use --request / --challenge / --evaluate / "
            "--outcome / --challenge-outcome"
        )


if __name__ == "__main__":
    main()
