"""HTTP bridge letting the Hex Hollow Godot game -- a separate project,
played on its own device on the same LAN -- trigger real reactions on
this physical cauldron: idle, it just bubbles under a chasing rainbow,
same as the main show's own idle look. Dropping an ingredient in splashes
and flashes the ring in that ingredient's color while a witch names it.
Finishing the spell announces success, then has all three witches recite
a fixed victory spell in turn before a victory swirl.

Talking to one of the three decorative companion witches in-game, or
inspecting an environmental landmark (a gravestone, a lantern, the
wishing well in the corn maze, etc.), has the matching real witch read
that exact line for real too -- see HOLLOW_COMPANION_LINES and
hollow_landmarks.HOLLOW_LANDMARKS. The corn maze (Hex Hollow's second
area) gets the same real-world treatment as the main hollow: a
splash/flash per treat collected, and a witch cheer plus a swirl on its
own completion.

Generic UI nudges ("satchel full," "look for ingredients," and the like)
deliberately do NOT get a real-world reading -- they fire constantly just
from wandering around, and voicing every one would make the physical
prop talk nonstop. Only the discrete, intentional interactions (an
ingredient/treat landing in the satchel, a witch or landmark spoken to,
a spell completed) trigger a reaction here.

See hex-hollow/autoload/cauldron_bridge.gd for the game-side caller,
which POSTs to the routes below.

Standalone, like trivia_game.py -- run this INSTEAD of
cauldron_controller.py's live show, not alongside it; both want the same
LED ring and audio hardware.

Run directly, then point the game's CauldronBridge.BASE_URL at whatever
address this prints:

    .venv/bin/python game_bridge.py
"""

from __future__ import annotations

import argparse
import asyncio
import random
import socket
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from aiohttp import web

import audio
import light_control
from hollow_landmarks import HOLLOW_LANDMARKS
from light_control import RGB
from voice_generator import VOICES, VoiceProfile, generate_to_path
from witches import WITCHES, Witch

CACHE_DIR = Path("generated/hollow")

INGREDIENT_COLORS: dict[str, RGB] = {
    "mushroom": RGB(255, 0, 25),
    "pumpkin": RGB(255, 50, 0),
    "skull": RGB(255, 223, 150),
    "feather": RGB(15, 40, 180),
    "essence": RGB(0, 255, 230),
    "moonflower": RGB(255, 40, 180),
}
"""This bridge's own palette -- picked to evoke each ingredient as it
actually looks in the game (e.g., the mooncap mushroom's red cap), not
synced from anywhere in the Godot project itself."""

VICTORY_SPELL: dict[Witch, str] = {
    Witch.VIOLET: "The simmer turns silver, the hollow takes flight,",
    Witch.AMBER: "The magic is loose and she's owning the night!",
    Witch.HAZEL: "Hmph. Put out the embers. It's far too bright.",
}
"""A fixed victory spell, one line per witch, recited in turn (Violet,
Amber, Hazel -- Witch's own iteration order) once the announcement
(WitchProfile.hollow_victory) is done -- same {witch: line}-in-order
recitation convention as cauldron_controller.recite_spell(). Gemini-
generated once, offline, and then kept fixed rather than regenerated
live: there's no photo here for a vision model to react to, so a fresh
API call on every victory would just reroll the same idea for no
benefit."""

TREAT_COLOR = RGB(230, 160, 40)
"""Warm harvest tone for the corn maze's treat splash -- distinct from
any single ingredient color since treats aren't tied to one."""

HOLLOW_COMPANION_LINES: dict[Witch, list[str]] = {
    Witch.VIOLET: [
        "Ahh, the moon is a silver spoon tonight, dear. Stir clockwise for luck.",
        "A little stardust makes every spell more scholarly, you know.",
        "Ahh, do mind the ghosts. They are terribly fond of interrupting a good ritual.",
    ],
    Witch.AMBER: [
        "Ooh, drop something spooky in the pot. Give it some real attitude!",
        "That cauldron's glowing again. Show-off.",
        "Ooh, if it hisses at you, just smile like you meant for that to happen.",
    ],
    Witch.HAZEL: [
        "Hmph. Keep your fingers out of the brew, child. It bites.",
        "The old ways favor patience, proper shoes, and fewer explosions.",
        "Mind the moonflower. It wilts when young witches hurry.",
    ],
}
"""Each companion witch in-game speaks her own hand-written lines (see
hollow_world.gd's COMPANION_DIALOGUE, which this must stay in sync with)
-- the real witch of the same name reads them for real, a direct 1:1
mapping rather than a random pick."""

HOLLOW_TREATS: dict[Witch, dict[str, str]] = {
    Witch.VIOLET: {
        "candy_apple": "Ahh, a candy apple, glossy as a spell gone right.",
        "caramel_corn": "Ahh, caramel corn, sweet and terribly sticky magic.",
        "candy_corn": "Ahh, candy corn, humble but devoted to tradition.",
    },
    Witch.AMBER: {
        "candy_apple": "Ooh, a candy apple! Shiny and ready to cause problems.",
        "caramel_corn": "Ooh, caramel corn! Don't even try to eat just one.",
        "candy_corn": "Ooh, candy corn! Underrated, honestly.",
    },
    Witch.HAZEL: {
        "candy_apple": "A candy apple. Hmph. Mind your teeth.",
        "caramel_corn": "Caramel corn. Sticky business, that.",
        "candy_corn": "Candy corn. Hmph. At least it doesn't melt.",
    },
}
"""One announcement per witch per fall treat (see corn_maze.gd's
TREAT_DATA) -- same shape as WitchProfile.hollow_ingredients, just kept
local to this bridge rather than the shared witch registry, since treats
are a Hex Hollow-specific concept, not a core witch trait."""

MAZE_VICTORY: dict[Witch, str] = {
    Witch.VIOLET: "Ahh, the harvest spell bubbles over at last. Well done, truly.",
    Witch.AMBER: "Ooh, you did it! The corn maze bows to you.",
    Witch.HAZEL: "Hmph. Not bad. The maze doesn't impress easily.",
}
"""A lighter-weight victory line for the corn maze's own completion --
one random witch, not the full three-witch VICTORY_SPELL recitation,
since this is a secondary/bonus area rather than the main game's
finale."""

HOUSE_KEEPSAKES: dict[Witch, dict[str, str]] = {
    Witch.VIOLET: {
        "silver_key": "Ahh, a silver key. Every lock remembers its own name.",
        "music_box": "Ahh, a music box. Wind it gently, it has waited so long to sing.",
        "ghost_candle": "Ahh, a ghost candle. Its flame burns colder than the room.",
        "velvet_ribbon": "Ahh, a velvet ribbon. Softness outlives even the manor's ghosts.",
    },
    Witch.AMBER: {
        "silver_key": "Ooh, a silver key! Bet it opens something it really shouldn't.",
        "music_box": "Ooh, a music box! Please don't wind it at midnight. Please.",
        "ghost_candle": "Ooh, a ghost candle! That flame is doing its own thing.",
        "velvet_ribbon": "Ooh, a velvet ribbon! Fancy, for a haunted house.",
    },
    Witch.HAZEL: {
        "silver_key": "A silver key. Hmph. Mind what it unlocks.",
        "music_box": "A music box. Hmph. Let it rest, this time.",
        "ghost_candle": "A ghost candle. Don't blow it out. It won't like that.",
        "velvet_ribbon": "A velvet ribbon. Hmph. At least it's tidy.",
    },
}
"""One announcement per witch per cursed keepsake (see haunted_house.gd's
ITEM_DATA) -- same shape as HOLLOW_TREATS."""

HOUSE_VICTORY: dict[Witch, str] = {
    Witch.VIOLET: "Ahh, four keepsakes gathered, and the manor finally rests.",
    Witch.AMBER: "Ooh, you actually did it! The whole house, done!",
    Witch.HAZEL: "Hmph. The manor's quiet now. About time.",
}
"""The Whispering Manor is the game's true ending (its own overlay says
"Happy Halloween!"), so its completion gets the same grand treatment as
the main hollow's own finale -- this announcement, then all three
witches recite HOUSE_VICTORY_SPELL below in turn -- rather than the corn
maze's lighter one-witch MAZE_VICTORY."""
HOUSE_VICTORY_SPELL: dict[Witch, str] = {
    Witch.VIOLET: "By silver key and candle's light,",
    Witch.AMBER: "the manor settles into the night.",
    Witch.HAZEL: "So mote it be. Sleep well. Good riddance, and good night.",
}
"""The spell recited after HOUSE_VICTORY's announcement -- see there."""


def _ingredient_audio_path(witch: Witch, ingredient_id: str) -> Path:
    return CACHE_DIR / f"{witch}_{ingredient_id}.mp3"


def _victory_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_victory.mp3"


def _spell_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_victory_spell.mp3"


def _companion_audio_path(witch: Witch, line_index: int) -> Path:
    return CACHE_DIR / f"{witch}_companion_{line_index}.mp3"


def _landmark_audio_path(area: str, landmark_id: str, line_index: int) -> Path:
    return CACHE_DIR / f"{area}_{landmark_id}_{line_index}.mp3"


def _treat_audio_path(witch: Witch, treat_id: str) -> Path:
    return CACHE_DIR / f"{witch}_{treat_id}.mp3"


def _maze_victory_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_maze_victory.mp3"


def _keepsake_audio_path(witch: Witch, keepsake_id: str) -> Path:
    return CACHE_DIR / f"{witch}_{keepsake_id}.mp3"


def _house_victory_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_house_victory.mp3"


def _house_spell_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_house_victory_spell.mp3"


def ensure_cache() -> None:
    """Synthesize any missing Hex Hollow voice lines, so a deposit or
    victory call never has to wait on ElevenLabs mid-game.

    Mirrors potion_play.ensure_cache(): collect what's missing, then
    synthesize it once, sequentially (ElevenLabs rejects a burst of
    concurrent requests).
    """
    missing: list[tuple[str, VoiceProfile, Path]] = []
    for witch in Witch:
        profile = WITCHES[witch]
        for ingredient_id, text in profile.hollow_ingredients.items():
            path = _ingredient_audio_path(witch, ingredient_id)
            if not path.exists():
                missing.append((text, VOICES[witch], path))
        victory_path = _victory_audio_path(witch)
        if not victory_path.exists():
            missing.append((profile.hollow_victory, VOICES[witch], victory_path))
        spell_path = _spell_audio_path(witch)
        if not spell_path.exists():
            missing.append((VICTORY_SPELL[witch], VOICES[witch], spell_path))
        for line_index, line in enumerate(HOLLOW_COMPANION_LINES[witch]):
            path = _companion_audio_path(witch, line_index)
            if not path.exists():
                missing.append((line, VOICES[witch], path))
        for treat_id, text in HOLLOW_TREATS[witch].items():
            path = _treat_audio_path(witch, treat_id)
            if not path.exists():
                missing.append((text, VOICES[witch], path))
        maze_victory_path = _maze_victory_audio_path(witch)
        if not maze_victory_path.exists():
            missing.append((MAZE_VICTORY[witch], VOICES[witch], maze_victory_path))
        for keepsake_id, text in HOUSE_KEEPSAKES[witch].items():
            path = _keepsake_audio_path(witch, keepsake_id)
            if not path.exists():
                missing.append((text, VOICES[witch], path))
        house_victory_path = _house_victory_audio_path(witch)
        if not house_victory_path.exists():
            missing.append((HOUSE_VICTORY[witch], VOICES[witch], house_victory_path))
        house_spell_path = _house_spell_audio_path(witch)
        if not house_spell_path.exists():
            missing.append((HOUSE_VICTORY_SPELL[witch], VOICES[witch], house_spell_path))
    for (area, landmark_id), (witch, lines) in HOLLOW_LANDMARKS.items():
        for line_index, line in enumerate(lines):
            path = _landmark_audio_path(area, landmark_id, line_index)
            if not path.exists():
                missing.append((line, VOICES[witch], path))

    if len(missing) == 0:
        return
    print(f"Synthesizing {len(missing)} Hex Hollow voice line(s).")

    async def _generate_all() -> None:
        for text, profile, path in missing:
            await generate_to_path(text, profile, path)

    asyncio.run(_generate_all())
    print("Hex Hollow voice cache ready.")


async def _react_to_deposit(ingredient_id: str) -> None:
    """Splash and flash the instant the ingredient lands, then have a
    random witch name it -- same "instant cue, then narration" shape as
    trivia_game.py's own submission splash.

    The flash briefly takes the ring over rather than overlaying on the
    idle rainbow (see main()) -- light_control.trigger_splash()'s
    scattered, brief pops were tried first, but a colored pop often
    blends into whatever hue the rainbow already happens to be cycling
    through at that pixel, unlike the white sparkle it was built for
    (white contrasts against any hue; a specific color often doesn't).
    A solid full-ring flash reads unmistakably as "that ingredient's
    color" instead. Rainbow resumes right after, same bracketing
    _react_to_victory() uses around its own exclusive lighting.
    """
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(None, light_control.stop_rainbow)
        # The splash SFX (~1.6s) runs noticeably longer than celebrate()'s
        # own blink animation (~0.7s) -- started here rather than gathered
        # below, so the rainbow resumes the instant the FLASH is done
        # instead of waiting on the (longer) sound too, which left the
        # ring sitting dark for the gap between the two.
        splash_sound = loop.run_in_executor(
            None, audio.play_file, audio.SPLASH_SFX_PATH
        )
        await loop.run_in_executor(
            None, light_control.celebrate, INGREDIENT_COLORS[ingredient_id]
        )
        await loop.run_in_executor(None, light_control.start_rainbow)
        await splash_sound
        await loop.run_in_executor(
            None,
            audio.play_file,
            _ingredient_audio_path(witch, ingredient_id),
            True,
            audio.WITCH_VOLUMES[witch],
        )


async def _react_to_victory() -> None:
    """Announce success, then recite the fixed victory spell (see
    VICTORY_SPELL) in each witch's own color before the swirl -- same
    "stop the rainbow only once something needs the ring to itself" shape
    trivia_game.run() uses for its own outcome lighting. Restarts the
    idle rainbow afterward, back to how main() left it.
    """
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(
            None,
            audio.play_file,
            _victory_audio_path(witch),
            True,
            audio.WITCH_VOLUMES[witch],
        )
        await loop.run_in_executor(None, light_control.stop_rainbow)
        for spell_witch in Witch:
            await loop.run_in_executor(
                None, light_control.set_leds, light_control.WITCH_COLORS[spell_witch]
            )
            await loop.run_in_executor(
                None,
                audio.play_file,
                _spell_audio_path(spell_witch),
                True,
                audio.WITCH_VOLUMES[spell_witch],
            )
        await loop.run_in_executor(None, light_control.victory_swirl)
        await loop.run_in_executor(None, light_control.start_rainbow)


async def _react_to_companion(witch_name: str, line_index: int) -> None:
    """Speak one companion witch's own line -- a quieter moment than a
    deposit, so it just plays over the idle rainbow/bubbling rather than
    taking the ring over."""
    try:
        witch = Witch(witch_name.lower())
    except ValueError:
        return
    lines = HOLLOW_COMPANION_LINES.get(witch)
    if (lines is None) or not (0 <= line_index < len(lines)):
        return
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(
            None,
            audio.play_file,
            _companion_audio_path(witch, line_index),
            True,
            audio.WITCH_VOLUMES[witch],
        )


async def _react_to_landmark(area: str, landmark_id: str, line_index: int) -> None:
    """Speak one environmental landmark's reading, by whichever witch is
    assigned to it (see HOLLOW_LANDMARKS) -- same quiet, lights-untouched
    treatment as _react_to_companion()."""
    entry = HOLLOW_LANDMARKS.get((area, landmark_id))
    if entry is None:
        return
    witch, lines = entry
    if not (0 <= line_index < len(lines)):
        return
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(
            None,
            audio.play_file,
            _landmark_audio_path(area, landmark_id, line_index),
            True,
            audio.WITCH_VOLUMES[witch],
        )


async def _react_to_treat(treat_id: str) -> None:
    """Same splash/flash/announce shape as _react_to_deposit() -- a
    shared harvest-orange flash (TREAT_COLOR) rather than a per-treat
    color, since the corn maze has no ingredient-color concept to match."""
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(None, light_control.stop_rainbow)
        splash_sound = loop.run_in_executor(
            None, audio.play_file, audio.SPLASH_SFX_PATH
        )
        await loop.run_in_executor(None, light_control.celebrate, TREAT_COLOR)
        await loop.run_in_executor(None, light_control.start_rainbow)
        await splash_sound
        await loop.run_in_executor(
            None,
            audio.play_file,
            _treat_audio_path(witch, treat_id),
            True,
            audio.WITCH_VOLUMES[witch],
        )


async def _react_to_maze_victory() -> None:
    """Lighter-weight than _react_to_victory() -- one random witch, one
    line, then a swirl, since the corn maze is a secondary/bonus area
    rather than the main game's finale."""
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(None, light_control.stop_rainbow)
        await loop.run_in_executor(
            None,
            audio.play_file,
            _maze_victory_audio_path(witch),
            True,
            audio.WITCH_VOLUMES[witch],
        )
        await loop.run_in_executor(None, light_control.victory_swirl)
        await loop.run_in_executor(None, light_control.start_rainbow)


async def _react_to_keepsake(keepsake_id: str) -> None:
    """Same splash/flash/announce shape as _react_to_deposit()/
    _react_to_treat() -- reuses TREAT_COLOR for the flash, since
    keepsakes have no ingredient-color concept of their own either."""
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(None, light_control.stop_rainbow)
        splash_sound = loop.run_in_executor(
            None, audio.play_file, audio.SPLASH_SFX_PATH
        )
        await loop.run_in_executor(None, light_control.celebrate, TREAT_COLOR)
        await loop.run_in_executor(None, light_control.start_rainbow)
        await splash_sound
        await loop.run_in_executor(
            None,
            audio.play_file,
            _keepsake_audio_path(witch, keepsake_id),
            True,
            audio.WITCH_VOLUMES[witch],
        )


async def _react_to_house_victory() -> None:
    """The game's true ending -- same grand announce-then-recite-then-
    swirl shape as _react_to_victory(), just with HOUSE_VICTORY/
    HOUSE_VICTORY_SPELL's own lines."""
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    async with _reaction_lock:
        await loop.run_in_executor(
            None,
            audio.play_file,
            _house_victory_audio_path(witch),
            True,
            audio.WITCH_VOLUMES[witch],
        )
        await loop.run_in_executor(None, light_control.stop_rainbow)
        for spell_witch in Witch:
            await loop.run_in_executor(
                None, light_control.set_leds, light_control.WITCH_COLORS[spell_witch]
            )
            await loop.run_in_executor(
                None,
                audio.play_file,
                _house_spell_audio_path(spell_witch),
                True,
                audio.WITCH_VOLUMES[spell_witch],
            )
        await loop.run_in_executor(None, light_control.victory_swirl)
        await loop.run_in_executor(None, light_control.start_rainbow)


_background_tasks: set[asyncio.Task[None]] = set()
"""asyncio only holds a weak reference to a task -- one with nothing else
referencing it can be garbage-collected mid-playback, cutting a witch off
partway through her line. _spawn() below keeps a strong reference until
the task finishes, for every fire-and-forget reaction."""

_reaction_lock = asyncio.Lock()
"""The final ingredient's /deposit and the /victory that immediately
follows it (see hollow_world.gd's interact()) arrive close enough
together that their reactions would otherwise run concurrently -- both
fighting over the same LED strip and the same pygame.mixer state
(audio.play_file() toggles pygame.mixer.quit()/init() around each
blocking call, which isn't safe from two threads at once). Every
reaction acquires this before touching any hardware, so a later one just
waits for the current one to finish instead of racing it."""


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _on_done(finished: asyncio.Task[None]) -> None:
        _background_tasks.discard(finished)
        # Fire-and-forget means nothing else ever awaits this task, so an
        # exception inside it would otherwise vanish silently (at best, a
        # terse "exception was never retrieved" warning easy to miss) --
        # print it plainly so a reaction that dies partway through is
        # actually visible on the console.
        if (not finished.cancelled()) and (finished.exception() is not None):
            print(f"Hex Hollow reaction failed: {finished.exception()!r}")

    task.add_done_callback(_on_done)


async def _handle_deposit(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    ingredient_id = payload.get("ingredient")
    if ingredient_id not in INGREDIENT_COLORS:
        print(f"Received /deposit with unknown ingredient: {ingredient_id!r}")
        return web.json_response({"error": "unknown ingredient"}, status=400)
    print(f"Received /deposit: {ingredient_id}")
    # Fire-and-forget: the game's own CauldronBridge call doesn't wait on
    # this either (see its REQUEST_TIMEOUT_SEC), so the response can go
    # back immediately rather than holding the connection open through a
    # few seconds of audio/light playback.
    _spawn(_react_to_deposit(ingredient_id))
    return web.json_response({"ok": True})


async def _handle_victory(_request: web.Request) -> web.Response:
    print("Received /victory")
    _spawn(_react_to_victory())
    return web.json_response({"ok": True})


async def _handle_companion(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    witch_name = payload.get("witch")
    line_index = payload.get("line")
    if not isinstance(witch_name, str) or not isinstance(line_index, int):
        return web.json_response({"error": "invalid payload"}, status=400)
    print(f"Received /companion: {witch_name} line {line_index}")
    _spawn(_react_to_companion(witch_name, line_index))
    return web.json_response({"ok": True})


async def _handle_landmark(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    area = payload.get("area")
    landmark_id = payload.get("landmark")
    line_index = payload.get("line")
    if (
        not isinstance(area, str)
        or not isinstance(landmark_id, str)
        or not isinstance(line_index, int)
    ):
        return web.json_response({"error": "invalid payload"}, status=400)
    print(f"Received /landmark: {area}/{landmark_id} line {line_index}")
    _spawn(_react_to_landmark(area, landmark_id, line_index))
    return web.json_response({"ok": True})


async def _handle_treat(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    treat_id = payload.get("treat")
    if treat_id not in HOLLOW_TREATS[Witch.VIOLET]:
        print(f"Received /treat with unknown treat: {treat_id!r}")
        return web.json_response({"error": "unknown treat"}, status=400)
    print(f"Received /treat: {treat_id}")
    _spawn(_react_to_treat(treat_id))
    return web.json_response({"ok": True})


async def _handle_maze_victory(_request: web.Request) -> web.Response:
    print("Received /maze_victory")
    _spawn(_react_to_maze_victory())
    return web.json_response({"ok": True})


async def _handle_house_event(request: web.Request) -> web.Response:
    """The Whispering Manor (Hex Hollow's third area) uses a single
    event-dispatch route rather than one route per interaction type --
    see cauldron_bridge.gd's notify_house_event(). Mirrors the other
    levels' reactions underneath; just a different wire shape in front
    of them."""
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    event = payload.get("event")
    print(f"Received /house_event: {event}")
    if event == "pickup":
        keepsake_id = payload.get("keepsake")
        if keepsake_id not in HOUSE_KEEPSAKES[Witch.VIOLET]:
            print(f"  unknown keepsake: {keepsake_id!r}")
            return web.json_response({"error": "unknown keepsake"}, status=400)
        _spawn(_react_to_keepsake(keepsake_id))
    elif event == "victory":
        _spawn(_react_to_house_victory())
    elif event == "landmark":
        landmark_id = payload.get("landmark")
        line_index = payload.get("line")
        if not isinstance(landmark_id, str) or not isinstance(line_index, int):
            return web.json_response({"error": "invalid payload"}, status=400)
        _spawn(_react_to_landmark("house", landmark_id, line_index))
    else:
        print(f"  unknown house event: {event!r}")
        return web.json_response({"error": "unknown event"}, status=400)
    return web.json_response({"ok": True})


def _local_ip() -> str:
    """Best-effort guess at this machine's LAN-facing IP, for printing a
    typeable address at startup. See trivia_game._local_ip() -- duplicated
    rather than imported, same reasoning as potion_play.py's _FACE_COLORS:
    keeps this standalone module free of any dependency on another one.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "localhost"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone HTTP bridge letting the Hex Hollow Godot "
        "game trigger real witch/light reactions on this cauldron."
    )
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()

    audio.setup()
    light_control.setup()
    ensure_cache()

    # Idle look/sound, same as the main show's own "nothing happening
    # yet" ambience -- runs continuously for as long as this bridge is
    # up, not just between reactions, so the cauldron reads as alive
    # even when nobody's playing. Bubbling runs as its own aplay
    # subprocess outside pygame.mixer (see audio.start_bubbling_background()),
    # so it never needs to stop for a witch's voice line to play.
    light_control.start_rainbow()
    audio.start_bubbling_background(audio.IDLE_BUBBLE_VOLUME)

    app = web.Application()
    app.router.add_post("/deposit", _handle_deposit)
    app.router.add_post("/victory", _handle_victory)
    app.router.add_post("/companion", _handle_companion)
    app.router.add_post("/landmark", _handle_landmark)
    app.router.add_post("/treat", _handle_treat)
    app.router.add_post("/maze_victory", _handle_maze_victory)
    app.router.add_post("/house_event", _handle_house_event)

    ip = _local_ip()
    print(f"Hex Hollow bridge listening on http://{ip}:{args.port}")
    print(
        "Point hex-hollow/autoload/cauldron_bridge.gd's BASE_URL at that "
        "address before playing."
    )
    sys.stdout.flush()
    web.run_app(app, host="0.0.0.0", port=args.port, print=None)


if __name__ == "__main__":
    main()
