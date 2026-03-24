"""Unit tests for the project."""

from matrix_premid import _get_best_mpris_activity, parse_mpris_data

# pylint: disable=missing-docstring,line-too-long


def test_parse_mpris_data_playing_song_with_artist():
    raw = "Playing❖Sea Of Feelings❖LIONE❖firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Listening to: Sea Of Feelings - LIONE"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_playing_youtube_music_suffix():
    raw = "Playing❖Sea Of Feelings - YouTube Music❖❖firefox"
    activity, title = parse_mpris_data(raw, "YouTube Music")
    assert activity == "Listening to: Sea Of Feelings | YouTube Music"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_paused_song():
    raw = "Paused❖Sea Of Feelings❖LIONE❖firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Paused: Sea Of Feelings - LIONE"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_html_entities():
    raw = "Playing❖Princess Chelsea &amp; Friends❖Princess Chelsea❖firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Listening to: Princess Chelsea & Friends - Princess Chelsea"
    assert title == "Princess Chelsea & Friends"


def test_get_best_mpris_activity_ignores_idle_youtube_music_when_paused():
    lines = [
        "Playing❖YouTube Music❖❖firefox",
        "Paused❖Awesome Song❖Awesome Artist❖plasma-browser-integration",
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The new behavior properly drops Paused songs so we get a clean Idle state
    assert activity == "Idle"
    assert title == ""


def test_get_best_mpris_activity_picks_highest_quality():
    lines = [
        "Playing❖YouTube Music❖❖firefox",
        "Playing❖Awesome Song❖Awesome Artist❖firefox",
        "Playing❖Basic Song Without Artist❖❖firefox",
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The Awesome Song has an artist, giving it quality=20+1=21
    assert activity == "Listening to: Awesome Song - Awesome Artist | YouTube Music"
    assert title == "Awesome Song"


def test_get_best_mpris_activity_inherits_youtube_music_across_players():
    """Test that rich players without YT Music inherit the tag from other tabs."""
    lines = [
        "Playing❖Eyes on Fire (Zeds Dead remix) | YouTube Music❖❖firefox",
        "Playing❖Eyes on Fire (Zeds Dead remix)❖Blue Foundation❖plasma-browser-integration",  # noqa: E501
    ]
    activity, title = _get_best_mpris_activity(lines)
    assert activity == (
        "Listening to: Eyes on Fire (Zeds Dead remix) - Blue Foundation | YouTube Music"  # noqa: E501
    )
    assert title == "Eyes on Fire (Zeds Dead remix)"
