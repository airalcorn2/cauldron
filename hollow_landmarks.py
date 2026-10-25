"""Environmental landmark readings for game_bridge.py's Hex Hollow bridge.

Kept in their own file, separate from game_bridge.py's actual bridge
logic, purely because there are 28 of them (across the hollow, corn
maze, and manor) and they'd otherwise dwarf everything else in that
module. See HOLLOW_LANDMARKS and game_bridge._react_to_landmark() for
how these get used.
"""

from __future__ import annotations

from witches import Witch

HOLLOW_LANDMARKS: dict[tuple[str, str], tuple[Witch, list[str]]] = {
    ("hollow", "gravestone_crooked"): (
        Witch.VIOLET,
        [
            "Here lies Reginald. He chased a ghost. The ghost won.",
            "He asked the moon for directions. It pointed down.",
        ],
    ),
    ("hollow", "gravestone_mossy"): (
        Witch.AMBER,
        [
            "Beloved Beatrice. She always said the grave was half full.",
            "Rest in peace. Please leave the offerings where they are.",
        ],
    ),
    ("hollow", "lantern"): (
        Witch.HAZEL,
        [
            "The flame went out years ago. Its shadow still flickers.",
            "Someone lit this lantern at midnight. Someone else answered.",
        ],
    ),
    ("hollow", "oak"): (
        Witch.VIOLET,
        [
            "The oak whispers your name, but only on the second listen.",
            "A face is carved in the bark. It looks more awake than you do.",
        ],
    ),
    ("hollow", "pond_sign"): (
        Witch.AMBER,
        [
            "POND RULES: No feeding the fish. They remember faces.",
            "The water is six inches deep. The reflection is much deeper.",
        ],
    ),
    ("hollow", "pumpkin_winking"): (
        Witch.HAZEL,
        [
            "This pumpkin has a heartbeat. It is trying to pretend it doesn't.",
            "The grin is painted on. The blink was not.",
        ],
    ),
    ("hollow", "pumpkin_smallest"): (
        Witch.VIOLET,
        [
            "It is holding a secret. It refuses to share.",
            "A tiny stem, a tiny curse, an enormous attitude.",
        ],
    ),
    ("corn_maze", "well"): (
        Witch.AMBER,
        [
            "Drop in a wish, not a witch. The last one clogged the bucket.",
            "Your reflection winks first. How terribly impolite.",
            "A voice below whispers: 'One penny for luck. Two for plausible deniability.'",
        ],
    ),
    ("corn_maze", "tree"): (
        Witch.HAZEL,
        [
            "The branches creak: 'I know every shortcut. I just enjoy watching you wander.'",
            "The lantern flickers twice. A leaf lands on your hat like a tiny blessing.",
            "'No broom parking,' says the tree. 'The squirrels have complained.'",
        ],
    ),
    ("corn_maze", "wagon"): (
        Witch.VIOLET,
        [
            "The delivery note reads: 'Three pumpkins, two curses. Handle with scare.'",
            "One pumpkin rolls an inch toward you. You politely pretend not to notice.",
            "The wheels turn without a breeze. Even the wagons go haunting after hours.",
        ],
    ),
    # The Whispering Manor (Hex Hollow's third/final area) -- 18
    # landmarks reusing manor_interactions.gd's exact text verbatim.
    ("house", "portrait"): (
        Witch.VIOLET,
        [
            "The lady in the frame sighs. 'Do straighten your hat. We have standards, even after death.'",
            "Her eyes follow you. 'Of course I stare, dear. Nobody brings me a book.'",
            "A painted hand points toward the foyer. 'Four keepsakes. One brew. Then kindly let us sleep.'",
        ],
    ),
    ("house", "piano"): (
        Witch.AMBER,
        [
            'One key plays by itself. The next note is a tiny, offended sneeze.',
            "A ghostly pianist whispers: 'Requests cost a shilling. Complaints cost two.'",
            'The sheet music is titled A Minor Inconvenience. Every note is a boo.',
        ],
    ),
    ("house", "clock"): (
        Witch.HAZEL,
        [
            "It strikes thirteen. A voice inside mutters: 'Daylight haunting time. Terrible business.'",
            "The pendulum swings backward. 'You are not late,' says the clock. 'Yesterday is early.'",
            'A little brass plaque reads: Winding optional. Existential dread included.',
        ],
    ),
    ("house", "oven"): (
        Witch.VIOLET,
        [
            "Something in the chimney asks for toast. 'Lightly frightened, please.'",
            'The kettle whistles a funeral march, then apologizes. It meant to make tea.',
            'A recipe card says: Add one pinch of salt. Do not add the cook. Again.',
        ],
    ),
    ("house", "supper_table"): (
        Witch.AMBER,
        [
            'The flowers look fresh, which is rude of them at this hour.',
            'Four candles burn bravely; none recalls who set the table.',
            'Dinner is late again. The ghosts blame the soup, naturally.',
        ],
    ),
    ("house", "sideboard"): (
        Witch.HAZEL,
        [
            'The crockery has survived three hauntings and one dreadful potluck.',
            'Pumpkins on parade, each grinning like it knows the punchline.',
            'A plate whispers that porcelain is simply crockery with ambitions.',
        ],
    ),
    ("house", "mirror"): (
        Witch.VIOLET,
        [
            'The oval shows a ghostly guest who insists she is your better side.',
            'Its glow is flattering, though it makes every eyebrow look haunted.',
            'A very pale reflection bows first. Good manners are not dead.',
        ],
    ),
    ("house", "bookshelf"): (
        Witch.AMBER,
        [
            'The forbidden books are shelved by danger, then alphabetically.',
            "One dusty volume warns, 'Do not open.' How refreshingly clear.",
            'Even the spines look nervous when the librarian is nearby.',
        ],
    ),
    ("house", "writing_desk"): (
        Witch.HAZEL,
        [
            'The abandoned notes end mid-sentence, then resume with better penmanship.',
            "Someone wrote, 'Dear diary,' and immediately lost their nerve.",
            'Ink stains gather like tiny ghosts with unfinished business.',
        ],
    ),
    ("house", "globe"): (
        Witch.VIOLET,
        [
            'The globe marks every place the manor has never been invited.',
            'Its oceans look calm; the continents are plainly plotting something.',
            'A careful spin reveals no country named Somewhere Sensible.',
        ],
    ),
    ("house", "fireplace"): (
        Witch.AMBER,
        [
            'The flames keep a warm little secret and refuse to share it.',
            'Ash settles like gray snow, very cozy and slightly judgmental.',
            'The hearth has heard every parlor tale and still wants one more.',
        ],
    ),
    ("house", "armchair"): (
        Witch.HAZEL,
        [
            'The green cushions sag as if someone invisible has just stood up.',
            'Apparently empty, though the upholstery has excellent posture.',
            'A chair this dignified must be hiding a very small aristocrat.',
        ],
    ),
    ("house", "dollhouse"): (
        Witch.VIOLET,
        [
            'The tiny house has a tiny foyer, for tiny guests with tiny secrets.',
            'Every window is dark, which is sensible for a house this small.',
            'The miniature parlor is tidier than ours. No one likes a show-off.',
        ],
    ),
    ("house", "toy_chest"): (
        Witch.AMBER,
        [
            'Toys spill out like a cheerful rebellion against sensible storage.',
            'A wooden drum hides beneath the heap, pretending innocence.',
            'The chest contains enough playthings to occupy one ghost for centuries.',
        ],
    ),
    ("house", "rocking_horse"): (
        Witch.HAZEL,
        [
            'The painted horse waits patiently for a rider with impeccable balance.',
            'Its wooden smile says it has crossed stranger rooms than this.',
            'A gallant steed, though its idea of galloping is gently rocking.',
        ],
    ),
    ("house", "bed"): (
        Witch.VIOLET,
        [
            'The plush rabbit keeps watch with the solemnity of a tiny guard.',
            'The blankets are tucked in neatly; even nightmares must knock first.',
            'A cozy bed, made for dreams and one very long-eared chaperone.',
        ],
    ),
    ("house", "stag"): (
        Witch.AMBER,
        [
            "The antlers crown the foyer like nature's most opinionated chandelier.",
            'This noble stag has witnessed every entrance and judged most of them.',
            'Dust rests on its brow, giving the old monarch a silver crown.',
        ],
    ),
    ("house", "pantry"): (
        Witch.HAZEL,
        [
            'The jars stand in ranks, each guarding a mystery with a label.',
            'Crockery crowds the shelves like gossiping aunts at a village feast.',
            'Some preserves are older than the house and considerably less chatty.',
        ],
    ),
}
"""Environmental landmark readings, reusing the exact text from each
landmark's own epitaphs/lines array in hollow_world.gd (area "hollow") or
corn_maze.gd (area "corn_maze") -- keyed (area, landmark id), since
landmark ids are only unique within one area, not across both. One witch
assigned per landmark (round robin, not random), so only that one
witch's voice needs caching per line rather than all three."""
