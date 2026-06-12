"""Keyword matching: definitions for exactly the terms a text uses."""

from sts_bench.tools.keyword_db import keyword_lines


def test_matches_keywords_in_rules_text():
    lines = keyword_lines(["Apply 1 Weak to ALL enemies. Exhaust."])
    assert any(line.startswith("Weak:") for line in lines)
    assert any(line.startswith("Exhaust:") for line in lines)


def test_each_keyword_defined_once_in_order_of_appearance():
    lines = keyword_lines(
        ["Apply 2 Vulnerable.", "Gain 5 Block.", "Apply 1 Vulnerable and 1 Weak."]
    )
    assert [line.split(":")[0] for line in lines] == ["Vulnerable", "Block", "Weak"]


def test_inflected_forms_map_to_the_same_keyword():
    weak = keyword_lines(["Enemies are Weakened."])
    assert len(weak) == 1 and weak[0].startswith("Weak:")


def test_word_boundaries_prevent_substring_hits():
    # "blocked" is not the Block keyword; "weakness" is not Weak
    assert keyword_lines(["The door is blockaded by weakness-proof vines."]) == []


def test_punctuated_names_match():
    lines = keyword_lines(["Channel 1 Dark.", "Trigger the passive ability of all Dark orbs."])
    assert any(line.startswith("Dark:") for line in lines)


def test_none_and_empty_texts_are_ignored():
    assert keyword_lines([None, "", "Deal 6 damage."]) == []
