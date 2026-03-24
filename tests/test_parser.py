"""Unit tests for the project."""

from matrix_premid import SEP_STR, _get_best_mpris_activity, parse_mpris_data

# pylint: disable=missing-docstring,line-too-long


def test_parse_mpris_data_playing_song_with_artist():
    raw = f"Playing{SEP_STR}Sea Of Feelings{SEP_STR}LIONE{SEP_STR}firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Listening to: Sea Of Feelings - LIONE"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_playing_youtube_music_suffix():
    raw = f"Playing{SEP_STR}Sea Of Feelings - YouTube Music{SEP_STR}{SEP_STR}firefox"
    activity, title = parse_mpris_data(raw, "YouTube Music")
    assert activity == "Listening to: Sea Of Feelings | YouTube Music"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_paused_song():
    raw = f"Paused{SEP_STR}Sea Of Feelings{SEP_STR}LIONE{SEP_STR}firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Paused: Sea Of Feelings - LIONE"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_html_entities():
    raw = f"Playing{SEP_STR}Princess Chelsea &amp; Friends{SEP_STR}Princess Chelsea{SEP_STR}firefox"  # noqa: E501
    activity, title = parse_mpris_data(raw)
    assert activity == "Listening to: Princess Chelsea & Friends - Princess Chelsea"
    assert title == "Princess Chelsea & Friends"


def test_get_best_mpris_activity_prioritizes_paused_over_idle():
    lines = [
        f"Playing{SEP_STR}YouTube Music{SEP_STR}{SEP_STR}firefox",
        f"Paused{SEP_STR}Awesome Song{SEP_STR}Awesome Artist{SEP_STR}plasma-browser-integration",  # noqa: E501
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The new behavior properly boosts Paused songs over empty Idle
    assert activity == "Paused: Awesome Song - Awesome Artist | YouTube Music"
    assert title == "Awesome Song"


def test_get_best_mpris_activity_picks_highest_quality():
    lines = [
        f"Playing{SEP_STR}YouTube Music{SEP_STR}{SEP_STR}firefox",
        f"Playing{SEP_STR}Awesome Song{SEP_STR}Awesome Artist{SEP_STR}firefox",
        f"Playing{SEP_STR}Basic Song Without Artist{SEP_STR}{SEP_STR}firefox",
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The Awesome Song has an artist, giving it quality=20+1=21
    assert activity == "Listening to: Awesome Song - Awesome Artist | YouTube Music"
    assert title == "Awesome Song"


def test_get_best_mpris_activity_inherits_youtube_music_across_players():
    """Test that rich players without YT Music inherit the tag from other tabs."""
    lines = [
        f"Playing{SEP_STR}Eyes on Fire (Zeds Dead remix) | YouTube Music{SEP_STR}{SEP_STR}firefox",  # noqa: E501
        f"Playing{SEP_STR}Eyes on Fire (Zeds Dead remix){SEP_STR}Blue Foundation{SEP_STR}plasma-browser-integration",  # noqa: E501
    ]
    activity, title = _get_best_mpris_activity(lines)
    assert activity == (
        "Listening to: Eyes on Fire (Zeds Dead remix) - Blue Foundation | YouTube Music"  # noqa: E501
    )
    assert title == "Eyes on Fire (Zeds Dead remix)"


def test_parse_mpris_data_youtube():
    raw = f"Playing{SEP_STR}Some Video{SEP_STR}{SEP_STR}firefox"
    act, title = parse_mpris_data(raw, "YouTube")
    assert act == "Watching: Some Video | YouTube"
    assert title == "Some Video"


def test_parse_mpris_data_netflix():
    raw = f"Playing{SEP_STR}Stranger Things{SEP_STR}{SEP_STR}firefox"
    act, title = parse_mpris_data(raw, "Netflix")
    assert act == "Watching: Stranger Things | Netflix"
    assert title == "Stranger Things"
