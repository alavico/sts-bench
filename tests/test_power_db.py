"""Pinned power text resolves, and the -1 sentinel is told apart from real stacks."""

from sts_bench.state.schema import Power
from sts_bench.tools.power_db import amount_is_sentinel, power_text


def make_power(id, name=None, amount=1):
    return Power.model_validate({"id": id, "name": name or id, "amount": amount})


def test_text_by_mod_id():
    assert power_text(make_power("Hex")) == (
        "Whenever you play a non-Attack card, shuffle X Dazed into your draw pile."
    )


def test_text_by_spaced_id():
    # wire ids are TitleCase with spaces; the database keys are SCREAMING_SNAKE
    assert "gain X Block" in power_text(make_power("Plated Armor"))


def test_corrected_descriptions():
    # upstream mangled literal numbers into X<digit>; the pinned data carries
    # hand-verified tooltips
    assert power_text(make_power("Split")) == (
        "When its HP is at or below 50%, splits into 2 smaller slimes with its current HP."
    )
    assert power_text(make_power("Vulnerable")) == (
        "Receives 50% more damage from Attacks. Lasts X turns."
    )


def test_unknown_power_has_no_text():
    assert power_text(make_power("Modded Nonsense")) is None


def test_sentinel_only_on_known_stackless_minus_one():
    # Split's -1 is the wire's "no stacks" placeholder
    assert amount_is_sentinel(make_power("Split", amount=-1))
    # Strength stacks: -1 is a genuine negative stack and must stay visible
    assert not amount_is_sentinel(make_power("Strength", amount=-1))
    # a real amount is never a sentinel, stackable or not (Vulnerable 2 = 2 turns)
    assert not amount_is_sentinel(make_power("Vulnerable", amount=2))
    assert not amount_is_sentinel(make_power("Hex", amount=1))
    # unknown powers keep their numbers rather than silently vanishing
    assert not amount_is_sentinel(make_power("Modded Nonsense", amount=-1))
