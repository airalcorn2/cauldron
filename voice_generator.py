"""ElevenLabs text-to-speech for the three witches.

Run directly to test the API. The test paths use the free voices and write one
<witch>.mp3 into generated/.

    .venv/bin/python voice_generator.py --list-voices                          # Voices your key can use.
    .venv/bin/python voice_generator.py --text "Bubble and toil"               # All three voices.
    .venv/bin/python voice_generator.py --text "Bubble and toil" --role amber  # One voice, one request.
    .venv/bin/python voice_generator.py --text "Bubble and toil" --sequential  # Three, one at a time.
    .venv/bin/python voice_generator.py --spell generated/spell.json           # One line per witch.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from witches import WITCHES, Witch, WitchProfile

ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICES_URL = "https://api.elevenlabs.io/v1/voices"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"
TTS_TIMEOUT_SEC = 10.0
OUTPUT_DIR = Path("generated")


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """ElevenLabs delivery controls.

    stability          Higher is steadier in pitch and rhythm; lower is more
                       expressive but erratic.
    similarity_boost   Higher tracks the source voice more closely.
    style              Higher exaggerates cadence and is the main cause of odd
                       lilt, so keep it low.
    speed              Range 0.7-1.2. 1.0 is natural; below 1.0 slows delivery.
    use_speaker_boost  Sharpens the voice at a small latency cost.
    """

    stability: float
    similarity_boost: float
    style: float
    speed: float = 1.0
    use_speaker_boost: bool = True

    def as_payload(self) -> dict[str, float | bool]:
        """Return the ``voice_settings`` object for the TTS request body."""
        return dataclasses.asdict(self)


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    """An ElevenLabs voice plus the delivery settings to use with it."""

    voice_id: str
    settings: VoiceSettings


def _voice_settings(profile: WitchProfile) -> VoiceSettings:
    """Lift a WitchProfile's plain delivery fields into a VoiceSettings."""
    return VoiceSettings(
        stability=profile.stability,
        similarity_boost=profile.similarity_boost,
        style=profile.style,
        speed=profile.speed,
        use_speaker_boost=profile.use_speaker_boost,
    )


# Premium voices from your ElevenLabs account. Used by the live controller.
VOICES: dict[Witch, VoiceProfile] = {
    w: VoiceProfile(p.voice_id, _voice_settings(p)) for w, p in WITCHES.items()
}

# Free premade voices for the test paths (this CLI and cauldron_controller.py
# --once). They must be voices your key can address: on the free tier an
# unavailable voice_id returns HTTP 402. Refresh them from --list-voices if a
# call fails.
FREE_VOICES: dict[Witch, VoiceProfile] = {
    w: VoiceProfile(p.free_voice_id, _voice_settings(p)) for w, p in WITCHES.items()
}


async def _generate_speech(
    session: aiohttp.ClientSession,
    text: str,
    profile: VoiceProfile,
    output_path: Path,
) -> None:
    url = ELEVENLABS_TTS_URL.format(voice_id=profile.voice_id)
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": profile.settings.as_payload(),
    }
    timeout = aiohttp.ClientTimeout(total=TTS_TIMEOUT_SEC)
    async with session.post(
        url, json=payload, headers=headers, timeout=timeout
    ) as resp:
        resp.raise_for_status()
        audio_bytes = await resp.read()

    output_path.write_bytes(audio_bytes)


async def generate_all_speech(
    lines: Mapping[Witch, str],
    voices: Mapping[Witch, VoiceProfile] = VOICES,
    *,
    sequential: bool = False,
) -> dict[Witch, Path]:
    """Synthesize one mp3 per witch line. Returns ``{witch: path}``.

    Concurrent by default; pass ``sequential=True`` to issue the requests one at
    a time, which avoids the free tier's low concurrent-request limit.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = {witch: OUTPUT_DIR / f"{witch}.mp3" for witch in lines}
    async with aiohttp.ClientSession() as session:
        coros = [
            _generate_speech(session, text, voices[witch], paths[witch])
            for witch, text in lines.items()
        ]
        if sequential:
            for coro in coros:
                await coro
        else:
            await asyncio.gather(*coros)
    return paths


def list_voices() -> None:
    """Print every voice this key can address, for populating FREE_VOICES."""
    req = urllib.request.Request(
        ELEVENLABS_VOICES_URL, headers={"xi-api-key": ELEVENLABS_API_KEY}
    )
    try:
        with urllib.request.urlopen(req, timeout=TTS_TIMEOUT_SEC) as resp:
            voices = json.load(resp)["voices"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        hint = ""
        if exc.code == 401 and "voices_read" in body:
            hint = (
                "\n\nYour API key is scoped and lacks 'voices_read'. Add that scope "
                "to the key (elevenlabs.io -> Settings -> API Keys), use an "
                "unrestricted key, or copy voice IDs off the Voices page on the "
                "website."
            )
        raise SystemExit(f"Could not list voices: {exc.code} {body}{hint}")

    for voice in sorted(voices, key=lambda v: (v["category"], v["name"].lower())):
        print(f"{voice['category']:12} {voice['voice_id']}  {voice['name']}")


def _lines_from_args(args: argparse.Namespace) -> dict[Witch, str]:
    """Build the ``{witch: line}`` mapping the CLI should synthesize."""
    if args.spell:
        raw = json.loads(Path(args.spell).read_text())
        return {Witch(key): value for key, value in raw.items()}
    if args.role:
        return {args.role: args.text}
    return {witch: args.text for witch in Witch}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate witch speech with the ElevenLabs API."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--text",
        help="Speak this one line. All three witch voices unless --role is given.",
    )
    source.add_argument(
        "--spell",
        type=Path,
        help=f"Path to a spell JSON file ({{{', '.join(Witch)}}}); one line per witch.",
    )
    source.add_argument(
        "--list-voices",
        action="store_true",
        help="List the voices your API key can use, then exit.",
    )
    parser.add_argument(
        "--role",
        type=Witch,
        choices=list(Witch),
        help="With --text: synthesize only this witch's voice (one request).",
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Issue the TTS requests one at a time instead of concurrently, "
        "to avoid the free tier's concurrent-request limit.",
    )
    args = parser.parse_args()

    if args.list_voices:
        list_voices()
        return
    if args.role and not args.text:
        parser.error("--role only applies to --text")

    lines = _lines_from_args(args)

    # Tests always use the free voices to avoid spending premium quota.
    paths = asyncio.run(
        generate_all_speech(lines, FREE_VOICES, sequential=args.sequential)
    )
    for witch, path in paths.items():
        print(f"{witch}: {path}")


if __name__ == "__main__":
    main()
