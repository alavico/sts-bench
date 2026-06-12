"""Digest mining: the report's parsers read combat turns and map choices back
out of the exact text the serializer emits, and shrug at everything else."""

from sts_bench.report.digest import combat_snapshot, map_choice

COMBAT_DIGEST = """\
<run>IRONCLAD (ascension 0) | act 1 floor 6 | HP 41/80 | gold 120 | deck 12 cards | relics 2 | potions (0/3): none</run>
<combat>
turn 3 | energy 3 | block 5
you: Vulnerable 1
enemy[0] Gremlin Nob 42/86 block 4 | intent ATTACK 16x1; Anger 2
enemy[1] Mad Gremlin 10/21 | intent not yet revealed
hand[0] Whirlwind (X)
hand[1] Slimed (1) [unplayable] -- Exhaust.
hand[2] Strike (1) [needs target]
piles: draw 4, discard 2, exhaust 1 (contents via tools)
</combat>
<commands>play_card, end_turn</commands>"""

MAP_DIGEST = """\
<run>IRONCLAD (ascension 0) | act 1 floor 4 | HP 71/80 | gold 50 | deck 11 cards | relics 2 | potions (0/3): none</run>
<screen type="MAP">
at node $ (x=3)
next: M (x=2), ? (x=4)
(symbols: M monster, E elite, $ shop, R rest, T chest, ? event, B boss; full layout via get_map)
</screen>
<choices>
[0] x=2
[1] x=4
</choices>
<commands>choose, return_back</commands>"""

BOSS_DIGEST = """\
<run>IRONCLAD (ascension 0) | act 1 floor 15 | HP 36/90 | gold 147 | deck 17 cards | relics 6 | potions (1/3): Weak Potion</run>
<screen type="MAP">
at node R (x=0)
boss fight available
(symbols: M monster, E elite, $ shop, R rest, T chest, ? event, B boss; full layout via get_map)
</screen>
<choices>
[0] boss
</choices>
<commands>choose, use_potion, discard_potion, return_back</commands>"""


def test_combat_snapshot_reads_turn_player_and_enemies():
    snap = combat_snapshot(COMBAT_DIGEST)
    assert snap is not None
    assert (snap.turn, snap.energy, snap.block) == (3, 3, 5)
    assert (snap.hp, snap.max_hp) == (41, 80)
    nob, gremlin = snap.enemies
    assert (nob.name, nob.hp, nob.max_hp, nob.block) == ("Gremlin Nob", 42, 86, 4)
    assert nob.intent == "ATTACK 16x1; Anger 2"
    assert (gremlin.name, gremlin.block) == ("Mad Gremlin", 0)
    assert gremlin.intent == "not yet revealed"


def test_combat_snapshot_none_outside_combat():
    assert combat_snapshot(MAP_DIGEST) is None
    assert combat_snapshot("<run>out of game (main menu)</run>") is None


def test_map_choice_reads_position_and_options():
    choice = map_choice(MAP_DIGEST)
    assert choice is not None
    assert choice.at_x == 3
    assert choice.boss is False
    assert choice.options == {0: 2, 1: 4}


def test_map_choice_boss_row_has_no_x_options():
    choice = map_choice(BOSS_DIGEST)
    assert choice is not None
    assert choice.boss is True
    assert choice.options == {}


def test_map_choice_none_off_the_map_screen():
    assert map_choice(COMBAT_DIGEST) is None
