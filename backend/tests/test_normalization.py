"""Speech normalization tests.

Every case here was a real defect heard during demo rehearsal. Speech bugs are
invisible in the transcript — the text reads correctly and only the audio is
wrong — so they need tests to stay fixed.
"""

from __future__ import annotations

import pytest

from app.providers.tts.normalization import normalize_for_speech as n


# --------------------------------------------------------------------------
# Phone numbers — spoken digit by digit
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Call the Building Department at (516) 465-4020 to apply.",   # paren + space
    "Call 516-465-4020 to apply.",                                # hyphens
    "Call 516.465.4020 to apply.",                                # dots
    "Call (516)465-4020 to apply.",                               # paren, no space
    "Call 516 465 4020 to apply.",                                # spaces
])
def test_phone_numbers_are_spelled_out(text):
    """Regression: "(516) 465-4020" was read as "five hundred sixteen, four
    hundred sixty five" — useless to anyone writing it down."""
    out = n(text)
    assert "five one six" in out
    assert "four six five" in out
    assert "four zero two zero" in out
    assert "516" not in out


def test_phone_does_not_swallow_the_preceding_space():
    """Regression: an over-eager pattern produced "Callfive one six"."""
    out = n("Call 516-465-4031 for the Recycling Center.")
    assert "Callfive" not in out
    assert "Call five one six" in out


def test_toll_free_keeps_the_country_code():
    out = n("Toll free 1-800-555-1234.")
    assert out.startswith("Toll free one, eight zero zero")
    assert "1-800" not in out


def test_two_phone_numbers_in_one_sentence():
    out = n("Public Works at (516) 465-4020, or Police at (516) 465-4100.")
    assert out.count("five one six") == 2
    assert "four zero two zero" in out and "four one zero zero" in out


@pytest.mark.parametrize("text", [
    "The 2026 budget was approved.",
    "The office is at 351 Stewart Avenue.",
    "The Village maintains 74 miles of streets.",
])
def test_ordinary_numbers_are_left_alone(text):
    """Only phone-shaped runs get spelled out; plain numbers read naturally."""
    assert n(text) == text


# --------------------------------------------------------------------------
# Times — spoken the way a person says them
# --------------------------------------------------------------------------

def test_times_use_natural_phrasing():
    """Regression: "6:00 a.m." was read "six, zero, zero, a m"."""
    out = n("Put trash out by 6:00 a.m. on collection day.")
    assert "six in the morning" in out
    assert "6:00" not in out and "A M" not in out


def test_evening_and_afternoon():
    assert "seven in the evening" in n("Not before 7:00 p.m.")
    assert "four thirty in the afternoon" in n("Closes at 4:30 PM.")


def test_noon_and_midnight():
    out = n("Open 12:00 p.m. to 12:00 a.m.")
    assert "noon" in out and "midnight" in out


def test_minutes_under_ten_say_oh():
    assert "six oh five" in n("The meeting starts at 6:05 p.m.")


# --------------------------------------------------------------------------
# Everything else
# --------------------------------------------------------------------------

def test_money_is_spoken_as_currency():
    out = n("The fee is $12.50 per container.")
    assert "12 dollars and 50 cents" in out
    assert "$" not in out


def test_urls_are_never_read_aloud():
    assert "the Village website" in n("Visit https://www.gardencityny.net for details.")
    assert "https" not in n("Visit https://www.gardencityny.net for details.")


def test_ranges_and_abbreviations():
    out = n("Open Mon-Fri, approx. 30% of the time.")
    assert "Monday to Friday" in out
    assert "approximately" in out
    assert "30 percent" in out


def test_normalization_is_idempotent():
    """Applying it twice must not corrupt already-normalized text."""
    once = n("Call (516) 465-4020 by 6:00 a.m.")
    assert n(once) == once


def test_empty_input_is_safe():
    assert n("") == ""
