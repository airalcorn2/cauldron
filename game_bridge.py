"""HTTP bridge letting the Hex Hollow Godot game -- a separate project,
played on its own device on the same LAN -- trigger real reactions on
this physical cauldron: idle, it just bubbles under a chasing rainbow,
same as the main show's own idle look. Dropping an ingredient in splashes
and flashes the ring in that ingredient's color while a witch names it.
Finishing the spell announces success, then has all three witches recite
a fixed victory spell in turn before a victory swirl. See
hex-hollow/autoload/cauldron_bridge.gd for the game-side caller, which
POSTs to the two routes below.

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
from light_control import RGB
from voice_generator import VOICES, VoiceProfile, generate_to_path
from witches import WITCHES, Witch

CACHE_DIR = Path("generated/hollow")

# Matches hex-hollow/scripts/game/hollow_world.gd's own per-ingredient
# palette, so the ring's flash actually reflects what's on screen.
INGREDIENT_COLORS: dict[str, RGB] = {
    "mushroom": RGB(201, 182, 255),
    "pumpkin": RGB(255, 138, 61),
    "skull": RGB(232, 226, 208),
    "feather": RGB(141, 124, 196),
    "essence": RGB(159, 232, 224),
    "moonflower": RGB(242, 201, 224),
}

# A fixed, hand-written victory spell, one line per witch, recited in
# turn (Violet, Amber, Hazel -- Witch's own iteration order) once the
# announcement (WitchProfile.hollow_victory) is done -- same {witch:
# line}-in-order recitation convention as
# cauldron_controller.recite_spell(), just hand-written rather than
# Gemini-generated: there's no photo here for a vision model to react to.
VICTORY_SPELL: dict[Witch, str] = {
    Witch.VIOLET: "By moonlight gathered, root and bone and bloom,",
    Witch.AMBER: "the hollow's magic chases out the gloom!",
    Witch.HAZEL: "So mote it be. Go on, then. Enjoy your little boom.",
}


def _ingredient_audio_path(witch: Witch, ingredient_id: str) -> Path:
    return CACHE_DIR / f"{witch}_{ingredient_id}.mp3"


def _victory_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_victory.mp3"


def _spell_audio_path(witch: Witch) -> Path:
    return CACHE_DIR / f"{witch}_victory_spell.mp3"


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

    The idle rainbow (see main()) keeps spinning the whole time --
    trigger_splash() only ever overlays on top of it, so there's nothing
    here to stop or restart.
    """
    witch = random.choice(list(Witch))
    loop = asyncio.get_running_loop()
    light_control.trigger_splash(INGREDIENT_COLORS[ingredient_id])
    await loop.run_in_executor(None, audio.play_file, audio.SPLASH_SFX_PATH)
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


# asyncio only holds a weak reference to a task -- one with nothing else
# referencing it can be garbage-collected mid-playback, cutting a witch
# off partway through her line. _spawn() below keeps a strong reference
# until the task finishes, for every fire-and-forget reaction.
_background_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _handle_deposit(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except ValueError:
        return web.json_response({"error": "invalid JSON"}, status=400)
    ingredient_id = payload.get("ingredient")
    if ingredient_id not in INGREDIENT_COLORS:
        return web.json_response({"error": "unknown ingredient"}, status=400)
    # Fire-and-forget: the game's own CauldronBridge call doesn't wait on
    # this either (see its REQUEST_TIMEOUT_SEC), so the response can go
    # back immediately rather than holding the connection open through a
    # few seconds of audio/light playback.
    _spawn(_react_to_deposit(ingredient_id))
    return web.json_response({"ok": True})


async def _handle_victory(_request: web.Request) -> web.Response:
    _spawn(_react_to_victory())
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
