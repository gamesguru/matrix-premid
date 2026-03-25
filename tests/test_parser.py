"""Unit tests for the project."""

from matrix_premid import SEP_STR, _get_best_mpris_activity, parse_mpris_data

# pylint: disable=missing-docstring,line-too-long


def test_get_best_mpris_activity_idle():
    assert _get_best_mpris_activity([]) == ("Idle", "")
    assert _get_best_mpris_activity(["", "   "]) == ("Idle", "")
    assert _get_best_mpris_activity(["Invalid Line"]) == ("Idle", "")


def test_parse_mpris_data_playing_song_with_artist():
    raw = f"Playing{SEP_STR}Sea Of Feelings{SEP_STR}LIONE{SEP_STR}firefox"
    activity, title = parse_mpris_data(raw)
    assert activity == "Listening to: Sea Of Feelings - LIONE"
    assert title == "Sea Of Feelings"


def test_parse_mpris_data_playing_youtube_music_suffix():
    url = "https://music.youtube.com/watch?v=123"
    content = f"Sea Of Feelings - YouTube Music{SEP_STR}{SEP_STR}firefox{SEP_STR}"
    raw = f"Playing{SEP_STR}{content}{url}"
    activity, title = parse_mpris_data(raw, "YouTube Music", url)
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


def test_parse_mpris_data_youtube_url_detection():
    # Test that even without "YouTube" in title, URL detects it
    url = "https://www.youtube.com/watch?v=abc"
    raw = (
        f"Playing{SEP_STR}Cool Video{SEP_STR}Cool Channel{SEP_STR}firefox{SEP_STR}{url}"
    )
    activity, title = parse_mpris_data(raw, url=url)
    assert activity == "Watching: Cool Video - Cool Channel | YouTube"
    assert title == "Cool Video"


def test_get_best_mpris_activity_prevents_poisoning():
    # YouTube Music is Paused, YouTube is Playing
    # In the old code, both would be identified as "YouTube Music"
    url_yt = "https://youtube.com/watch?v=1"
    url_ym = "https://music.youtube.com/watch?v=2"
    lines = [
        f"Playing{SEP_STR}Cool Video - YouTube{SEP_STR}{SEP_STR}firefox{SEP_STR}{url_yt}",
        f"Paused{SEP_STR}Some Song{SEP_STR}Artist{SEP_STR}firefox{SEP_STR}{url_ym}",
    ]
    activity, title = _get_best_mpris_activity(lines)
    assert activity == "Watching: Cool Video | YouTube"
    assert title == "Cool Video"


def test_get_best_mpris_activity_prioritizes_paused_over_idle():
    url = "https://music.youtube.com/watch?v=3"
    lines = [
        f"Playing{SEP_STR}YouTube Music{SEP_STR}{SEP_STR}firefox{SEP_STR}{url}",
        (
            f"Paused{SEP_STR}Awesome Song{SEP_STR}Awesome Artist"
            f"{SEP_STR}plasma-integration{SEP_STR}"
        ),  # noqa: E501
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The new behavior properly boosts Paused songs over empty Idle
    assert activity == "Paused: Awesome Song - Awesome Artist | YouTube Music"
    assert title == "Awesome Song"


def test_get_best_mpris_activity_picks_highest_quality():
    lines = [
        f"Playing{SEP_STR}YouTube Music{SEP_STR}{SEP_STR}firefox{SEP_STR}",
        f"Playing{SEP_STR}Awesome Song{SEP_STR}Awesome Artist{SEP_STR}firefox{SEP_STR}",
        f"Playing{SEP_STR}Basic Song Without Artist{SEP_STR}{SEP_STR}firefox{SEP_STR}",
    ]
    activity, title = _get_best_mpris_activity(lines)
    # The Awesome Song has an artist, giving it quality=20
    # In first pass, it won't have provider unless title matches.
    # But Awesome Song doesn't match "YouTube Music".
    # Wait, if we don't have provider, quality is just 20.
    # If we DO have provider (via inheritance), quality is 21.
    assert "Awesome Song" in activity
    assert "Awesome Artist" in activity


def test_get_best_mpris_activity_inherits_youtube_music_across_players():
    """Test that rich players without YT Music inherit the tag from other tabs."""
    lines = [
        f"Playing{SEP_STR}Eyes on Fire (Zeds Dead remix) | YouTube Music{SEP_STR}{SEP_STR}firefox{SEP_STR}",  # noqa: E501
        f"Playing{SEP_STR}Eyes on Fire (Zeds Dead remix){SEP_STR}Blue Foundation{SEP_STR}plasma-browser-integration{SEP_STR}",  # noqa: E501
    ]
    activity, title = _get_best_mpris_activity(lines)
    assert activity == (
        "Listening to: Eyes on Fire (Zeds Dead remix) - Blue Foundation | YouTube Music"  # noqa: E501
    )
    assert title == "Eyes on Fire (Zeds Dead remix)"


def test_parse_mpris_data_youtube():
    url = "https://youtube.com/watch?v=v"
    raw = f"Playing{SEP_STR}Some Video{SEP_STR}{SEP_STR}firefox{SEP_STR}{url}"
    act, title = parse_mpris_data(raw, "YouTube", url=url)
    assert act == "Watching: Some Video | YouTube"
    assert title == "Some Video"


def test_parse_mpris_data_netflix_url_detection():
    url = "https://www.netflix.com/watch/123"
    raw = f"Playing{SEP_STR}Stranger Things{SEP_STR}{SEP_STR}firefox{SEP_STR}{url}"
    activity, title = parse_mpris_data(raw, url=url)
    assert activity == "Watching: Stranger Things | Netflix"
    assert title == "Stranger Things"


def test_parse_mpris_data_twitch_url_detection():
    url = "https://www.twitch.tv/some_streamer"
    raw = f"Playing{SEP_STR}Some Stream{SEP_STR}{SEP_STR}firefox{SEP_STR}{url}"
    activity, title = parse_mpris_data(raw, url=url)
    assert activity == "Watching: Some Stream | Twitch"
    assert title == "Some Stream"


def test_parse_mpris_data_netflix():
    raw = f"Playing{SEP_STR}Stranger Things{SEP_STR}{SEP_STR}firefox"
    act, title = parse_mpris_data(raw, "Netflix")
    assert act == "Watching: Stranger Things | Netflix"
    assert title == "Stranger Things"
