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

    name: str  # Display name, e.g., "Violet".
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
    # Hand-written mode-select dialogue (see cauldron_controller.py's
    # _run_mode_selection()): one line asking the mortal to pick a mode,
    # one line per mode naming that mode's button (keyed by mode name --
    # "react", "story", "joke", "prophecy", "request", "category"), and one
    # confirmation line per mode. All cached to disk and never generated
    # live, so opening mode selection never has to wait on the network.
    mode_select_intro: str
    mode_select_options: dict[str, str]
    mode_select_confirmations: dict[str, str]
    # Hand-written lines for the "brew" mini-game's outcome (see
    # brew_game.py / cauldron_controller.process_brew_game()), spoken by
    # whichever witch asked the mode-select question. Also cached, never
    # generated live -- there's no photo to react to, so a vision-model
    # spell doesn't apply here.
    brew_victory: str
    brew_failure: str
    # Hand-written lines for the "play" mini-mode (see potion_play.py):
    # one per ingredient (keyed "{color}_{index}", 6 each for blue/red/
    # yellow/green -- a shared name list wrapped in this witch's own
    # phrasing, not independently written per witch), one per heat level
    # (3, index 0-2, none/medium/vigorous), one for stirring (either talon
    # -- direction isn't narrated, just the gesture), and one each for the
    # two special ingredients (D-pad left/right): a rainbow (floods every
    # pixel at once) and a comet (sweeps around, clearing tint as it
    # passes). Also cached, never generated live.
    potion_ingredients: dict[str, str]
    potion_heat: list[str]
    potion_stir: str
    potion_rainbow: str
    potion_comet: str
    speed: float = 1.0
    use_speaker_boost: bool = True
    # Per-witch playback volume, since some voices render quieter than others
    # at the same ElevenLabs settings. Not capped at 1.0 -- audio.py applies
    # this as a real amplification factor on mpg123's decoder, so values
    # above 1 genuinely boost past the source's original level (at the risk
    # of clipping/distortion if pushed too far). audio.py reads this via its
    # own WITCH_VOLUMES, the same way light_control.py reads color via
    # WITCH_COLORS. Tune with `audio.py --role <witch> --voice-volume` to A/B
    # a value before setting it here.
    volume: float = 0.3


# The single source of truth for every witch. To add, remove, or restyle a
# witch, edit this dict (and the Witch enum above); light_control.py's
# WITCH_COLORS, voice_generator.py's VOICES/FREE_VOICES, audio.py's
# WITCH_VOLUMES, prompts.py's persona text, and cauldron_controller.py's
# MODE_SELECT_INTROS/OPTIONS/CONFIRMATIONS are all derived from it.
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
        mode_select_intro=(
            "Ahh, a mortal seeks to alter the ritual! Behold the eight runes "
            "upon your magic tablet."
        ),
        mode_select_options={
            "react": "Tap the blue rune for a spell.",
            "story": "Tap the red rune for a story.",
            "joke": "Tap the yellow rune for a jest.",
            "prophecy": "Tap the green rune for a prophecy.",
            "request": "Press the left talon to fetch ingredients.",
            "category": "Press the right talon for a challenge of properties.",
            "brew": "Tap the upper rune to test your mastery of the brew.",
            "play": "Tap the lower rune to simply play with the light.",
        },
        mode_select_confirmations={
            "react": "The spell-rune it is. Drop your offering, and I shall weave it into verse.",
            "story": "The story-rune it is. Let us see what tale your offering tells.",
            "joke": "The jest-rune it is. Prepare yourself for mirth most wicked.",
            "prophecy": "The prophecy-rune it is. The omens shall now be read.",
            "request": "The left talon it is. We shall name our price in ingredients.",
            "category": "The right talon it is. A riddle of properties awaits you.",
            "brew": "The upper rune it is. Balance the fire and the froth, or the cauldron shall have its due.",
            "play": "The lower rune it is. Let the colors answer to your hand, with no price to pay.",
        },
        brew_victory="Magnificent! The brew holds steady -- you are a true alchemist.",
        brew_failure="Alas, the brew is lost. The fire and the froth answer to no one tonight.",
        potion_ingredients={
            "blue_0": "Ahh, frost lily petals!",
            "blue_1": "Ahh, moonstone dust!",
            "blue_2": "Ahh, sapphire beetle shells!",
            "blue_3": "Ahh, winter wisp essence!",
            "blue_4": "Ahh, blueberry eyeballs!",
            "blue_5": "Ahh, icicle shavings!",
            "red_0": "Ahh, dragon's blood resin!",
            "red_1": "Ahh, redcap mushroom caps!",
            "red_2": "Ahh, phoenix feather ash!",
            "red_3": "Ahh, crimson scorpion venom!",
            "red_4": "Ahh, firebrand chili dust!",
            "red_5": "Ahh, goblin's blush powder!",
            "yellow_0": "Ahh, goblin gold flakes!",
            "yellow_1": "Ahh, sulfur brimstone!",
            "yellow_2": "Ahh, canary feather down!",
            "yellow_3": "Ahh, lemon pixie dust!",
            "yellow_4": "Ahh, sunflower witch pollen!",
            "yellow_5": "Ahh, amber resin shavings!",
            "green_0": "Ahh, goblin snot!",
            "green_1": "Ahh, swamp moss spores!",
            "green_2": "Ahh, venomous nettle leaves!",
            "green_3": "Ahh, emerald beetle wings!",
            "green_4": "Ahh, toadstool spores!",
            "green_5": "Ahh, nightshade clippings!",
        },
        potion_heat=[
            "The flame dies to nothing.",
            "A gentle simmer takes hold.",
            "A furious, roiling boil!",
        ],
        potion_stir="Ahh, give it a stir!",
        potion_rainbow="Ahh, a rainbow of colors!",
        potion_comet="Ahh, a comet streaks through, wiping the slate clean!",
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
        mode_select_intro=(
            "Ooh, somebody wants options! Check it, your tablet's got eight runes."
        ),
        mode_select_options={
            "react": "Blue's for a spell.",
            "story": "Red's for a story.",
            "joke": "Yellow's for a joke.",
            "prophecy": "Green's for a prophecy.",
            "request": "Left talon if you wanna fetch stuff.",
            "category": "Right talon for a challenge.",
            "brew": "Upper rune's the brewing game.",
            "play": "Lower rune's just for playing around.",
        },
        mode_select_confirmations={
            "react": "Spell mode, got it. Toss something in and let's see what magic we cook up.",
            "story": "Story mode, nice. Let's see what tale your junk tells.",
            "joke": "Joke mode, hehe. This is gonna be good.",
            "prophecy": "Prophecy mode, ooh spooky. Let's peek at your future.",
            "request": "Fetching mode. Get ready, I'm gonna make you work for it.",
            "category": "Challenge mode. Let's see if you can actually pull this off.",
            "brew": "Brewing game, let's go. Keep it together or it's gonna get messy.",
            "play": "Play mode, nice and chill. Go wild, no pressure.",
        },
        brew_victory="Whoa, you actually did it! Master alchemist right here.",
        brew_failure="Yikes, it blew up. Better luck next time, rookie.",
        potion_ingredients={
            "blue_0": "Ooh, frost lily petals!",
            "blue_1": "Ooh, moonstone dust!",
            "blue_2": "Ooh, sapphire beetle shells!",
            "blue_3": "Ooh, winter wisp essence!",
            "blue_4": "Ooh, blueberry eyeballs!",
            "blue_5": "Ooh, icicle shavings!",
            "red_0": "Ooh, dragon's blood resin!",
            "red_1": "Ooh, redcap mushroom caps!",
            "red_2": "Ooh, phoenix feather ash!",
            "red_3": "Ooh, crimson scorpion venom!",
            "red_4": "Ooh, firebrand chili dust!",
            "red_5": "Ooh, goblin's blush powder!",
            "yellow_0": "Ooh, goblin gold flakes!",
            "yellow_1": "Ooh, sulfur brimstone!",
            "yellow_2": "Ooh, canary feather down!",
            "yellow_3": "Ooh, lemon pixie dust!",
            "yellow_4": "Ooh, sunflower witch pollen!",
            "yellow_5": "Ooh, amber resin shavings!",
            "green_0": "Ooh, goblin snot!",
            "green_1": "Ooh, swamp moss spores!",
            "green_2": "Ooh, venomous nettle leaves!",
            "green_3": "Ooh, emerald beetle wings!",
            "green_4": "Ooh, toadstool spores!",
            "green_5": "Ooh, nightshade clippings!",
        },
        potion_heat=[
            "Flame's out.",
            "Ooh, a nice simmer.",
            "Whoa, full boil!",
        ],
        potion_stir="Ooh, stir it up!",
        potion_rainbow="Ooh, a whole rainbow!",
        potion_comet="Ooh, a comet! Watch it clean house!",
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
        mode_select_intro=(
            "Hmph. Changing the ritual, are we. Eight choices on that little "
            "tablet of yours."
        ),
        mode_select_options={
            "react": "Blue rune, for a spell.",
            "story": "Red rune, for a story.",
            "joke": "Yellow rune, for a joke.",
            "prophecy": "Green rune, for a prophecy.",
            "request": "Left talon, to fetch ingredients.",
            "category": "Right talon, for a challenge.",
            "brew": "Upper rune, if you fancy yourself a brewer.",
            "play": "Lower rune, if you just want to fiddle with it.",
        },
        mode_select_confirmations={
            "react": "Spell mode. Fine. Give me something to work with.",
            "story": "Story mode. Hmph. Let's hear what your rubbish has to say.",
            "joke": "Joke mode. Don't expect it to be gentle.",
            "prophecy": "Prophecy mode. The future rarely flatters anyone.",
            "request": "Fetching mode. Go on then, don't keep me waiting.",
            "category": "Challenge mode. Let's see if you're clever enough.",
            "brew": "Brewing game. Hmph. Let's see if you've got the hands for it.",
            "play": "Playtime. Hmph. Fine, amuse yourself.",
        },
        brew_victory="Well. You kept it from boiling over. Don't let it go to your head.",
        brew_failure="Ruined. Hmph. I expected as much.",
        potion_ingredients={
            "blue_0": "Frost lily petals. Hmph.",
            "blue_1": "Moonstone dust. Hmph.",
            "blue_2": "Sapphire beetle shells. Hmph.",
            "blue_3": "Winter wisp essence. Hmph.",
            "blue_4": "Blueberry eyeballs. Hmph.",
            "blue_5": "Icicle shavings. Hmph.",
            "red_0": "Dragon's blood resin. Hmph.",
            "red_1": "Redcap mushroom caps. Hmph.",
            "red_2": "Phoenix feather ash. Hmph.",
            "red_3": "Crimson scorpion venom. Hmph.",
            "red_4": "Firebrand chili dust. Hmph.",
            "red_5": "Goblin's blush powder. Hmph.",
            "yellow_0": "Goblin gold flakes. Hmph.",
            "yellow_1": "Sulfur brimstone. Hmph.",
            "yellow_2": "Canary feather down. Hmph.",
            "yellow_3": "Lemon pixie dust. Hmph.",
            "yellow_4": "Sunflower witch pollen. Hmph.",
            "yellow_5": "Amber resin shavings. Hmph.",
            "green_0": "Goblin snot. Hmph.",
            "green_1": "Swamp moss spores. Hmph.",
            "green_2": "Venomous nettle leaves. Hmph.",
            "green_3": "Emerald beetle wings. Hmph.",
            "green_4": "Toadstool spores. Hmph.",
            "green_5": "Nightshade clippings. Hmph.",
        },
        potion_heat=[
            "No flame. Fine.",
            "A simmer. Hmph.",
            "A boil. Don't say I didn't warn you.",
        ],
        potion_stir="Stirring. Fine.",
        potion_rainbow="A rainbow. Fine, showy.",
        potion_comet="A comet. Hmph, clearing the mess.",
        speed=0.92,
        volume=1.5,  # Renders quieter than Violet/Amber at the same settings.
    ),
}
