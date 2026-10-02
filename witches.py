"""Shared domain types for the interactive cauldron.

WITCHES below is the single place to add, remove, or restyle a witch: its
display name, personality (folded into the Gemini prompts), LED color, and
ElevenLabs voice settings all live there. Every other module reads from the
registry -- or a view derived from it, like light_control.py's WITCH_COLORS
or voice_generator.py's VOICES/FREE_VOICES -- rather than naming a witch's
properties directly, so a change here propagates everywhere automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Witch(StrEnum):
    """The three witches, in the order they recite.

    A ``StrEnum`` so members double as their wire names: ``Witch("violet")``
    parses, ``str(Witch.VIOLET)`` is ``"violet"``, and iteration is in the
    order defined here. Adding a witch means adding a member here and a
    matching entry in WITCHES below.
    """

    VIOLET = "violet"
    AMBER = "amber"
    HAZEL = "hazel"


@dataclass(frozen=True, slots=True)
class WitchProfile:
    """Everything that distinguishes one witch from another.

    The stability/similarity_boost/style/speed/use_speaker_boost fields are
    ElevenLabs delivery controls, shared by both voice_id and free_voice_id
    below (the same delivery, just addressed to a different voice). See
    voice_generator.VoiceSettings, which these become, for what each one does.
    """

    name: str  # Display name, e.g. "Violet".
    personality: str  # Short phrase folded into the Gemini prompts.
    # This witch's LED color, as plain (r, g, b) values rather than
    # light_control.py's RGB type, so this module stays free of any hardware
    # dependency; light_control.py wraps this into its own RGB object.
    color: tuple[int, int, int]
    voice_id: str  # Premium ElevenLabs voice, used by the live show.
    free_voice_id: str  # Free-tier voice, used by tests and --once.
    # ElevenLabs delivery controls, plain rather than a voice_generator.
    # VoiceSettings object so this module stays free of any ElevenLabs API
    # dependency; voice_generator.py wraps these into its own VoiceSettings.
    stability: float
    similarity_boost: float
    style: float
    speed: float = 1.0
    use_speaker_boost: bool = True
    # Per-witch playback volume (0.0-1.0+), since some voices render quieter
    # than others at the same ElevenLabs settings. audio.py reads this via its
    # own WITCH_VOLUMES, the same way light_control.py reads color via
    # WITCH_COLORS. Tune with `audio.py <path> --voice-volume` to A/B a value
    # before setting it here.
    volume: float = 0.3


# The single source of truth for every witch. To add, remove, or restyle a
# witch, edit this dict (and the Witch enum above); light_control.py's
# WITCH_COLORS, voice_generator.py's VOICES/FREE_VOICES, and prompts.py's
# persona text are all derived from it.
WITCHES: dict[Witch, WitchProfile] = {
    Witch.VIOLET: WitchProfile(
        name="Violet",
        personality="whimsical and academic",
        color=(147, 0, 211),
        voice_id="sssn4wp3AspuK2kvy3Ym",  # "Vivien - Mysterious Witch"
        free_voice_id="XrExE9yKIg1WjnnlVkGX",
        stability=0.5,
        similarity_boost=0.75,
        style=0.35,
        speed=0.95,
        volume=0.65,
    ),
    Witch.AMBER: WitchProfile(
        name="Amber",
        personality="youthful and sassy",
        color=(255, 40, 0),
        voice_id="5PWbsfogbLtky5sxqtBz",  # "Nora - Evil Halloween Witch"
        free_voice_id="EXAVITQu4vr4xnSDxMaL",
        stability=0.45,
        similarity_boost=0.75,
        style=0.45,
        speed=1.0,
        volume=0.65,
    ),
    Witch.HAZEL: WitchProfile(
        name="Hazel",
        personality="old and crotchety",
        color=(60, 200, 40),
        voice_id="7NsaqHdLuKNFvEfjpUno",  # "Seer Morganna - Intimidating, and Clear"
        free_voice_id="Xb7hH8MSUJpSbSDYk0k2",
        stability=0.5,
        similarity_boost=0.7,
        style=0.3,
        speed=0.92,
        volume=0.95,  # Renders quieter than Violet/Amber at the same settings.
    ),
}
