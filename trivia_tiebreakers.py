"""Hand-curated, individually sourced numeric trivia questions for
trivia_game.py's sudden-death tiebreaker.

Kept in their own file, separate from trivia_game.py's actual game
logic, purely because there are 50 of them and they'd otherwise dwarf
everything else in that module. See NumericTiebreaker and
trivia_game._play_tiebreaker() for how these get used.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NumericTiebreaker:
    """A sudden-death question with a single, verifiable numeric answer --
    both teams guess at once (see trivia_game._play_tiebreaker()), and
    whichever guess is closer wins outright. ``source`` is shown
    alongside the real answer once the tiebreaker resolves, so the room
    can see it's not made up."""

    question: str
    answer: float
    source: str


# Hand-picked and fact-checked against the cited source -- unlike every
# other clue in this game (pulled from the Jeopardy dataset, whose own
# accuracy isn't this project's to vouch for), a tiebreaker only ever
# fires live in front of a room with a tied score, so it has to hold up
# to someone looking it up on the spot. One is picked at random each time
# a tiebreaker fires.
#
# A few entries (franchise film counts, the NRF Halloween-spending figure)
# are explicitly scoped to "as of" a date or year rather than phrased as
# eternally true -- re-verify those specific ones before reusing this list
# many years from now, since a sequel or a new survey can move the number.
TIEBREAKER_QUESTIONS = (
    NumericTiebreaker(
        question=(
            "According to historical court records from the Salem witch "
            "trials of 1692, exactly how many people were formally "
            "executed for witchcraft during the trials?"
        ),
        answer=19.0,
        source="Wikipedia: Salem witch trials -- 19 hanged (a 20th, Giles "
        "Corey, was separately pressed to death for refusing to enter a "
        "plea, which isn't counted as a formal execution).",
    ),
    NumericTiebreaker(
        question=(
            "In the Pendle witch trials in Lancashire, England, in 1612, "
            "exactly how many people were hanged for witchcraft?"
        ),
        answer=10.0,
        source="Wikipedia: Pendle witches -- 9 hanged together at "
        "Lancaster on 20 August 1612, plus a 10th (Jennet Preston) hanged "
        "separately at York on 29 July 1612.",
    ),
    NumericTiebreaker(
        question=(
            "How many victims make up the \"canonical five\" murders "
            "attributed to Jack the Ripper in Whitechapel, London, in 1888?"
        ),
        answer=5.0,
        source="Wikipedia: Jack the Ripper -- the historians' consensus "
        "\"canonical five\"; some researchers attribute more or fewer "
        "murders to him.",
    ),
    NumericTiebreaker(
        question=(
            "How many murders has the Zodiac Killer, active in Northern "
            "California in the late 1960s, been officially confirmed by "
            "investigators to have committed?"
        ),
        answer=5.0,
        source="Wikipedia: Zodiac Killer -- 5 confirmed; he claimed 37 "
        "victims in letters to police, never substantiated.",
    ),
    NumericTiebreaker(
        question=(
            "How many murders was serial killer John Wayne Gacy convicted "
            "of in Illinois?"
        ),
        answer=33.0,
        source="Wikipedia: John Wayne Gacy.",
    ),
    NumericTiebreaker(
        question=(
            "Jeffrey Dahmer was convicted of murder on how many total "
            "counts, combining his Wisconsin and Ohio killings?"
        ),
        answer=16.0,
        source="Wikipedia: Jeffrey Dahmer -- 15 counts in Wisconsin plus "
        "1 in Ohio.",
    ),
    NumericTiebreaker(
        question=(
            "Dennis Rader, the \"BTK\" serial killer active in Wichita, "
            "Kansas, was convicted of first-degree murder on how many "
            "counts?"
        ),
        answer=10.0,
        source="Wikipedia: Dennis Rader.",
    ),
    NumericTiebreaker(
        question=(
            "Shortly before his 1989 execution, how many murders did Ted "
            "Bundy confess to committing?"
        ),
        answer=30.0,
        source="Wikipedia: Ted Bundy -- he was legally convicted of only "
        "3; the true total is widely believed to be higher, but 30 is his "
        "own documented confession figure.",
    ),
    NumericTiebreaker(
        question=(
            "How many known victims are attributed to Ian Brady and Myra "
            "Hindley, the \"Moors Murderers,\" near Manchester, England, "
            "between 1963 and 1965?"
        ),
        answer=5.0,
        source="Wikipedia: Moors murders -- one victim, Keith Bennett, "
        "has never been found, but is still counted among the 5 known "
        "victims.",
    ),
    NumericTiebreaker(
        question=(
            "How many people were murdered at the Hinterkaifeck farm in "
            "Bavaria, Germany, on the night of March 31, 1922, in a still-"
            "unsolved case?"
        ),
        answer=6.0,
        source="Wikipedia: Hinterkaifeck murders.",
    ),
    NumericTiebreaker(
        question=(
            "How many people were killed in the still-unsolved Villisca "
            "axe murders in Iowa on the night of June 9-10, 1912?"
        ),
        answer=8.0,
        source="Wikipedia: Villisca axe murders -- the 6 Moore family "
        "members plus 2 child houseguests.",
    ),
    NumericTiebreaker(
        question=(
            "How many confirmed deaths are attributed to Mary Mallon, "
            "\"Typhoid Mary,\" the asymptomatic typhoid carrier in early-"
            "1900s New York?"
        ),
        answer=3.0,
        source="Wikipedia: Mary Mallon -- she's believed to have infected "
        "several dozen people total; only 3 of those infections resulted "
        "in confirmed deaths.",
    ),
    NumericTiebreaker(
        question=(
            "According to the official Bills of Mortality, how many "
            "plague deaths were recorded in London during the Great "
            "Plague of 1665?"
        ),
        answer=68_596.0,
        source="Wikipedia: Great Plague of London -- the officially "
        "recorded figure; historians believe underreporting means the "
        "true toll was substantially higher.",
    ),
    NumericTiebreaker(
        question=(
            "Of the 87 members of the Donner Party who set out across "
            "the Sierra Nevada in the winter of 1846-47, exactly how many "
            "died?"
        ),
        answer=39.0,
        source="Wikipedia: Donner Party -- 48 of 87 survived.",
    ),
    NumericTiebreaker(
        question="According to Wikipedia, there are at least how many "
        "known species of bat worldwide?",
        answer=1_500.0,
        source="Wikipedia: Bat -- \"at least 1,500 known species\"; bat "
        "taxonomy is still being refined, and other sources cite "
        "slightly lower figures.",
    ),
    NumericTiebreaker(
        question=(
            "How many living species of vampire bat -- bats that feed "
            "solely on blood -- are currently recognized?"
        ),
        answer=3.0,
        source="Wikipedia: Vampire bat -- the common, hairy-legged, and "
        "white-winged vampire bats.",
    ),
    NumericTiebreaker(
        question=(
            "As of January 2026, how many spider species have been "
            "formally described and catalogued by the World Spider "
            "Catalog, the standard taxonomic reference for spiders?"
        ),
        answer=53_656.0,
        source="Wikipedia: World Spider Catalog -- as of January 10, "
        "2026, after an average discovery rate of three new species per "
        "day.",
    ),
    NumericTiebreaker(
        question=(
            "How many species are currently recognized in the genus "
            "Latrodectus, the true black widow spiders found around the "
            "world?"
        ),
        answer=35.0,
        source="Wikipedia: Latrodectus -- as of October 2025; the "
        "taxonomy is actively being revised, with older sources citing "
        "31-34.",
    ),
    NumericTiebreaker(
        question=(
            "According to the International Ornithological Committee's "
            "classification, how many total species of owl, including "
            "extinct ones, are recognized in the order Strigiformes?"
        ),
        answer=254.0,
        source="Wikipedia: List of owl species -- 5 of the 254 are "
        "extinct.",
    ),
    NumericTiebreaker(
        question=(
            "According to Guinness World Records, what is the maximum "
            "recorded weight, in grams, of the goliath birdeater, the "
            "world's heaviest spider species?"
        ),
        answer=175.0,
        source="Guinness World Records: Largest spider.",
    ),
    NumericTiebreaker(
        question=(
            "What is the longest recorded fang length, in millimeters, "
            "of any venomous snake -- a record held by the Gaboon viper?"
        ),
        answer=55.0,
        source="Wikipedia: Gaboon viper.",
    ),
    NumericTiebreaker(
        question=(
            "In what year did the phrase \"trick or treat\" first appear "
            "in print, in a Canadian newspaper's account of Halloween "
            "mischief?"
        ),
        answer=1917.0,
        source="Wikipedia: Trick-or-treating -- first recorded in "
        "Ontario, in the Sault Daily Star, November 1, 1917 (as "
        "\"tricks or treats\").",
    ),
    NumericTiebreaker(
        question=(
            "In what year did the first known image of a pumpkin "
            "jack-o'-lantern, as opposed to a turnip one, appear in the "
            "United States?"
        ),
        answer=1867.0,
        source="National Geographic, citing historian Cindy Ott -- an "
        "image in an 1867 issue of Harper's Weekly.",
    ),
    NumericTiebreaker(
        question="In what year was the Ouija board patented in the "
        "United States?",
        answer=1891.0,
        source="Wikipedia: Ouija -- patent granted February 10, 1891, to "
        "Elijah Bond.",
    ),
    NumericTiebreaker(
        question=(
            "Eastern State Penitentiary in Philadelphia, now a famous "
            "\"haunted\" tourist attraction, operated continuously as a "
            "working prison for how many years before closing in 1971?"
        ),
        answer=142.0,
        source="Wikipedia: Eastern State Penitentiary -- operational "
        "from 1829 until 1971.",
    ),
    NumericTiebreaker(
        question=(
            "Alcatraz Island in San Francisco Bay operated as a U.S. "
            "federal penitentiary for how many years, from 1934 until "
            "its closure in 1963?"
        ),
        answer=29.0,
        source="National Park Service -- \"During the subsequent 29 "
        "years as a federal penitentiary, Alcatraz held 1500 men.\"",
    ),
    NumericTiebreaker(
        question=(
            "The sprawling, maze-like Winchester Mystery House in San "
            "Jose, California, is commonly said to have how many rooms?"
        ),
        answer=160.0,
        source="Click Americana -- the figure most consistently cited "
        "across sources, though the exact count has long been disputed "
        "since the house was built without a master plan.",
    ),
    NumericTiebreaker(
        question="Not counting a variable number of small sesamoid "
        "bones, how many bones are in one human hand?",
        answer=27.0,
        source="Wikipedia: Hand.",
    ),
    NumericTiebreaker(
        question=(
            "A human baby is born with roughly how many separate bones, "
            "before many of them fuse together over the following years "
            "into the 206 bones of an adult skeleton?"
        ),
        answer=270.0,
        source="Wikipedia: Human skeleton -- some popular secondary "
        "sources instead cite 300.",
    ),
    NumericTiebreaker(
        question=(
            "According to Guinness World Records, what is the combined "
            "length, in centimeters, of the longest fingernails ever "
            "grown on a pair of hands by a woman?"
        ),
        answer=1_306.58,
        source="Guinness World Records: Longest fingernails on a pair of "
        "hands (female) -- Diana Armstrong (USA), verified in "
        "Minneapolis, March 13, 2022.",
    ),
    NumericTiebreaker(
        question=(
            "According to a long-running University of Washington study, "
            "crows that have been mistreated by a specific person can "
            "hold a grudge against that individual's face for up to how "
            "many years?"
        ),
        answer=17.0,
        source="University of Washington urban wildlife research -- a "
        "study led by Professor John Marzluff, begun in 2006.",
    ),
    NumericTiebreaker(
        question=(
            "John Carpenter's original Halloween (1978) was made for "
            "about $325,000. In dollars, how much did it gross worldwide "
            "in theaters?"
        ),
        answer=70_000_000.0,
        source="Wikipedia: Halloween (1978 film) infobox.",
    ),
    NumericTiebreaker(
        question="In dollars, how much has The Exorcist (1973) grossed "
        "worldwide across all its theatrical releases?",
        answer=441_300_000.0,
        source="Wikipedia: The Exorcist infobox.",
    ),
    NumericTiebreaker(
        question=(
            "Across every theatrical release and re-release combined, "
            "how much has Steven Spielberg's Jaws (1975) grossed "
            "worldwide, in dollars?"
        ),
        answer=495_000_000.0,
        source="Wikipedia: Jaws (film) infobox.",
    ),
    NumericTiebreaker(
        question=(
            "In minutes, what is the theatrical runtime of 2017's It, "
            "starring Bill Skarsgard as Pennywise?"
        ),
        answer=135.0,
        source="Wikipedia: It (2017 film) infobox.",
    ),
    NumericTiebreaker(
        question=(
            "The Blair Witch Project (1999) was shot on an original "
            "production budget of roughly $60,000. In dollars, how much "
            "did it go on to gross worldwide at the box office?"
        ),
        answer=248_600_000.0,
        source="Wikipedia: The Blair Witch Project infobox.",
    ),
    NumericTiebreaker(
        question=(
            "In its original 1986 Viking hardcover first edition, how "
            "many pages long is Stephen King's novel It?"
        ),
        answer=1_138.0,
        source="Wikipedia: It (novel) infobox -- page count is edition-"
        "specific; later paperback/ebook editions differ.",
    ),
    NumericTiebreaker(
        question=(
            "In its original 1977 Doubleday hardcover first edition, how "
            "many pages long is Stephen King's The Shining?"
        ),
        answer=447.0,
        source="Wikipedia: The Shining (novel) infobox -- page count is "
        "edition-specific.",
    ),
    NumericTiebreaker(
        question="Not counting companion novellas, how many main novels "
        "make up Stephen King's The Dark Tower series?",
        answer=8.0,
        source="Wikipedia: The Dark Tower (series).",
    ),
    NumericTiebreaker(
        question=(
            "R.L. Stine's original Goosebumps book series ran from 1992 "
            "to 1997. How many numbered books were in that original run?"
        ),
        answer=62.0,
        source="Wikipedia: Goosebumps -- refers to the original numbered "
        "series, not Goosebumps 2000 or later spin-off lines.",
    ),
    NumericTiebreaker(
        question=(
            "Counting the 2010 remake and the crossover Freddy vs. "
            "Jason, how many theatrically released feature films make up "
            "the A Nightmare on Elm Street franchise?"
        ),
        answer=9.0,
        source="Wikipedia: A Nightmare on Elm Street (franchise).",
    ),
    NumericTiebreaker(
        question=(
            "Counting the crossover Freddy vs. Jason and the 2009 "
            "reboot, how many theatrically released feature films make "
            "up the Friday the 13th franchise?"
        ),
        answer=12.0,
        source="Wikipedia: Friday the 13th (franchise).",
    ),
    NumericTiebreaker(
        question="As of October 2026, how many Scream feature films "
        "have been released in theaters?",
        answer=7.0,
        source="Wikipedia: Scream (franchise) -- time-sensitive; an 8th "
        "film is in early development but unreleased as of this count.",
    ),
    NumericTiebreaker(
        question=(
            "Not counting the Spiral spinoff, how many mainline, "
            "numbered Saw feature films have been released in theaters, "
            "as of October 2026?"
        ),
        answer=9.0,
        source="Wikipedia: Saw (franchise) -- time-sensitive; an 11th "
        "mainline film was announced but is unreleased as of this count.",
    ),
    NumericTiebreaker(
        question=(
            "What is the Guinness World Record number for most lit "
            "jack-o'-lanterns displayed in one place at once, set by "
            "Keene, New Hampshire, in 2013?"
        ),
        answer=30_581.0,
        source="Guinness World Records: Most lit jack-o'-lanterns "
        "displayed.",
    ),
    NumericTiebreaker(
        question=(
            "What is the Guinness World Record number of people for the "
            "largest gathering of people dressed as zombies, set in "
            "Minneapolis in 2014?"
        ),
        answer=15_458.0,
        source="Guinness World Records: Largest gathering of zombies.",
    ),
    NumericTiebreaker(
        question=(
            "What is the Guinness World Record number of people for the "
            "largest gathering of people dressed as witches, set in "
            "Spain in 2013?"
        ),
        answer=1_607.0,
        source="Guinness World Records.",
    ),
    NumericTiebreaker(
        question=(
            "According to the National Confectioners Association, about "
            "how many pounds of candy corn are produced in the United "
            "States every year?"
        ),
        answer=35_000_000.0,
        source="Wikipedia: Candy corn, citing a National Confectioners "
        "Association figure -- an approximate industry figure, not an "
        "annually updated statistic.",
    ),
    NumericTiebreaker(
        question=(
            "According to the National Retail Federation's 2023 survey, "
            "how much did Americans spend in total on Halloween that "
            "year, in dollars?"
        ),
        answer=12_200_000_000.0,
        source="National Retail Federation, 2023 press release -- a "
        "record at the time; NRF publishes a new, higher figure most "
        "years, so this is scoped specifically to 2023.",
    ),
    NumericTiebreaker(
        question=(
            "Alfred Hitchcock's Psycho (1960) was shot in black-and-"
            "white with his TV crew to save money. What was its exact "
            "production budget, to the dollar?"
        ),
        answer=806_947.0,
        source="Wikipedia: Psycho (1960 film) infobox.",
    ),
)
