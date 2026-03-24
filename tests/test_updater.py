"""Tests for MatrixStatusUpdater and monitor_mpris."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from matrix_premid import SEP_STR, MatrixStatusUpdater, main, monitor_mpris


@pytest.fixture(autouse=True)
def patch_sleep():
    """Bypass asyncio.sleep delays globally for fast test execution."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


def test_updater_init():
    """Test the updater initializes the nio client correctly."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
        mock_client.assert_called_with("http://mock", "@test:mock")
        assert updater.client.access_token == "tok"


@pytest.mark.asyncio
async def test_updater_update():
    """Test pushing presence state to the Matrix room."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
        await updater.update("Listening to: Song | YT Music")
        updater.client.set_presence.assert_awaited_with(
            presence="online", status_msg="Listening to: Song | YT Music"
        )


@pytest.mark.asyncio
async def test_updater_update_paused():
    """Test a paused song yields presence correctly."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("mock", "mock", "mock")
        await updater.update("Paused: Song - Artist | YT Music")
        updater.client.set_presence.assert_awaited()


@pytest.mark.asyncio
async def test_updater_update_empty():
    """Test empty string correctly defaults to Idle status."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "mock", "mock")
        await updater.update("")
        updater.client.set_presence.assert_awaited_with(
            presence="online", status_msg="Idle"
        )


@pytest.mark.asyncio
async def test_updater_update_other():
    """Test non-music activities receive correct base quality attributes."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "mock", "mock")
        await updater.update("Watching: Movie", title="Movie")
        updater.client.set_presence.assert_awaited()


@pytest.mark.asyncio
async def test_updater_update_exception():
    """Test updating surviving network timeouts bounds."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("mock", "mock", "mock")
        updater.client.set_presence.side_effect = asyncio.TimeoutError()
        await updater.update("Listening to: Song")
        updater.client.set_presence.assert_awaited()


@pytest.mark.asyncio
async def test_updater_update_same_song_ignored():
    """Test ignoring unchanged song status strings."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
        updater.last_activity = "Listening to: Song"
        updater.last_title = "Song"
        await updater.update("Listening to: Song", title="Song")
        updater.client.set_presence.assert_not_called()


@pytest.mark.asyncio
@patch("matrix_premid.asyncio.create_subprocess_exec")
async def test_monitor_mpris_picks_best_activity(mock_exec):
    """Test MPRIS subprocess parsing defaults best output cleanly."""
    mock_proc = AsyncMock()
    mock_proc.communicate.side_effect = [
        (
            f"Playing{SEP_STR}Awesome Song{SEP_STR}"
            f"Awesome Artist{SEP_STR}firefox\n".encode("utf-8"),
            b"",
        ),
        Exception("Break loop"),
    ]
    mock_exec.return_value = mock_proc

    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("mock", "mock", "mock")
        updater.update = AsyncMock()
        try:
            await monitor_mpris(updater)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        updater.update.assert_awaited_with(
            "Listening to: Awesome Song - Awesome Artist", title="Awesome Song"
        )


@pytest.mark.asyncio
@patch("matrix_premid.sys.exit")
@patch("matrix_premid.shutil.which", return_value="/usr/bin/playerctl")
@patch("matrix_premid.acquire_lock")
@patch("matrix_premid.HOMESERVER", None)
@patch("matrix_premid.USERNAME", None)
@patch("matrix_premid.ACCESS_TOKEN", None)
@patch("matrix_premid.AsyncClient")
async def test_main_missing_env(_mock_client, _mock_lock, _mock_which, mock_exit):
    """Test main script breaks when Env details are lacking."""
    mock_exit.side_effect = SystemExit()
    try:
        await main()
    except SystemExit:
        pass
    mock_exit.assert_called_with(1)


@pytest.mark.asyncio
@patch("matrix_premid.sys.exit")
@patch("matrix_premid.shutil.which", return_value="/usr/bin/playerctl")
@patch("matrix_premid.acquire_lock")
@patch("matrix_premid.HOMESERVER", "mock")
@patch("matrix_premid.USERNAME", "@user")
@patch("matrix_premid.ACCESS_TOKEN", "tok")
@patch("matrix_premid.DEVICE_ID", "dev")
async def test_main_execution_mocked_gather(_mock_lock, _mock_which, mock_exit):
    """Test main entrypoint setups everything cleanly resolving without errors."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        mock_client.return_value = mock_instance

        mock_event = MagicMock()
        mock_event.user_id = "@user"
        mock_event.presence = "online"

        mock_resp = MagicMock()
        mock_resp.presence.events = [mock_event]

        # Emit native valid presence payload once, then gracefully exit loop
        mock_instance.sync.side_effect = [mock_resp, asyncio.CancelledError()]

        with patch(
            "matrix_premid.MatrixStatusUpdater.update", new_callable=AsyncMock
        ) as mock_update:
            mock_update.side_effect = asyncio.CancelledError()

            with patch("matrix_premid.asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate.side_effect = asyncio.CancelledError()
                mock_exec.return_value = mock_proc

                await main()
        # Use unused vars assertions to cleanly please pylint explicitly
        mock_exit.assert_not_called()


@pytest.mark.asyncio
async def test_updater_close():
    """Test shutdown cleanup of updater client sockets."""
    with patch("matrix_premid.AsyncClient") as mock_client:
        mock_client.return_value = AsyncMock()
        updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
        await updater.close()
        # pylint: disable=no-member
        updater.client.close.assert_awaited()
