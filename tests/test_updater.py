"""Tests for MatrixStatusUpdater and monitor_mpris."""

# pylint: disable=protected-access,no-member

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from matrix_premid.__main__ import (
    SEP_STR,
    MatrixStatusUpdater,
    main,
    monitor_mpris,
    install_service,
)


@pytest.fixture(autouse=True)
def patch_sleep():
    """Bypass asyncio.sleep delays globally for fast test execution."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


def test_updater_init():
    """Test the updater initializes correctly."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    assert updater.homeserver == "http://mock"
    assert updater.username == "@test:mock"
    assert updater.access_token == "tok"
    assert updater.device_id == "dev"


@pytest.mark.asyncio
async def test_updater_update():
    """Test pushing presence state to the Matrix room."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.return_value.__aenter__.return_value.status = 200
        mock_get_session.return_value = mock_session

        await updater.update("Listening to: Song | YT Music")
        await updater._update_task

        # Verify Presence Update was called via aiohttp
        mock_session.put.assert_any_call(
            "http://mock/_matrix/client/v3/presence/@test:mock/status",
            json=ANY,
            headers=ANY,
            timeout=ANY,
        )


@pytest.mark.asyncio
async def test_updater_update_paused():
    """Test a paused song yields presence correctly."""
    updater = MatrixStatusUpdater("http://mock", "mock", "tok")
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.return_value.__aenter__.return_value.status = 200
        mock_get_session.return_value = mock_session

        await updater.update("Paused: Song - Artist | YT Music")
        await updater._update_task
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_empty():
    """Test empty string is ignored by default (new behavior)."""
    updater = MatrixStatusUpdater("http://mock", "mock", "tok")
    await updater.update("")
    assert updater._update_task is None


@pytest.mark.asyncio
async def test_updater_update_other():
    """Test non-music activities receive correct base quality attributes."""
    updater = MatrixStatusUpdater("http://mock", "mock", "tok")
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.return_value.__aenter__.return_value.status = 200
        mock_get_session.return_value = mock_session

        await updater.update("Watching: Movie", title="Movie")
        await updater._update_task
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_exception():
    """Test updating surviving network errors."""
    updater = MatrixStatusUpdater("http://mock", "mock", "tok")
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("Network Error")
        mock_get_session.return_value = mock_session

        await updater.update("Listening to: Song")
        await updater._update_task
        # Should not crash despite the network error
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_same_song_ignored():
    """Test ignoring unchanged song status strings."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    updater.last_activity = "Listening to: Song"
    updater.last_title = "Song"
    await updater.update("Listening to: Song", title="Song")
    assert updater._update_task is None


@pytest.mark.asyncio
@patch("matrix_premid.__main__.asyncio.create_subprocess_exec")
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

    updater = MatrixStatusUpdater("http://mock", "mock", "tok")
    updater.update = AsyncMock()
    try:
        await monitor_mpris([updater], 5)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    updater.update.assert_awaited_with(
        "Listening to: Awesome Song - Awesome Artist", title="Awesome Song"
    )


@pytest.mark.asyncio
@patch("matrix_premid.__main__.sys.exit")
@patch("matrix_premid.__main__.shutil.which", return_value="/usr/bin/playerctl")
@patch("matrix_premid.__main__.os.path.exists", return_value=False)
async def test_main_missing_env(_mock_exists, _mock_which, mock_exit):
    """Test main script breaks when config is lacking."""
    mock_exit.side_effect = SystemExit()
    with patch("sys.argv", ["matrix_premid.py"]):
        try:
            await main()
        except SystemExit:
            pass
    mock_exit.assert_called_with(1)


@pytest.mark.asyncio
@patch("matrix_premid.__main__.sys.exit")
@patch("matrix_premid.__main__.shutil.which", return_value="/usr/bin/playerctl")
@patch("matrix_premid.__main__.acquire_lock")
@patch("matrix_premid.__main__.os.path.exists", return_value=True)
@patch("matrix_premid.__main__.json.load")
@patch("matrix_premid.__main__.keyring.get_password", return_value="mock_token")
@patch("matrix_premid.__main__.open", new_callable=MagicMock)
async def test_main_execution_mocked_gather(
    _mock_open,
    _mock_keyring,
    mock_json,
    _mock_exists,
    _mock_lock,
    _mock_which,
    mock_exit,
):
    """Test main entrypoint setups everything cleanly resolving without errors."""
    mock_json.return_value = {
        "accounts": [{"homeserver": "mock", "username": "@user", "device_id": "dev"}]
    }

    with patch("matrix_premid.__main__.asyncio.Event.wait", new_callable=AsyncMock):
        with patch(
            "matrix_premid.__main__.MatrixStatusUpdater.update",
            new_callable=AsyncMock,
        ) as mock_update:
            mock_update.side_effect = asyncio.CancelledError()

            with patch(
                "matrix_premid.__main__.asyncio.create_subprocess_exec"
            ) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.communicate.side_effect = asyncio.CancelledError()
                mock_exec.return_value = mock_proc

                with patch("sys.argv", ["matrix_premid.py"]):
                    await main()
    # Use unused vars assertions to cleanly please pylint explicitly
    mock_exit.assert_not_called()


@pytest.mark.asyncio
async def test_updater_close():
    """Test shutdown cleanup of updater client sockets."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    updater._session = MagicMock()
    updater._session.closed = False
    updater._session.close = AsyncMock()

    await updater.close()
    updater._session.close.assert_awaited()


@pytest.mark.asyncio
async def test_main_debug_flag():
    """Test the --debug flag in main sets log level."""
    with (
        patch("sys.argv", ["matrix_premid.py", "--debug"]),
        patch("matrix_premid.__main__.shutil.which", return_value="/usr/bin/playerctl"),
        patch("matrix_premid.__main__.MatrixStatusUpdater") as mock_updater_class,
        patch("matrix_premid.__main__.logging.basicConfig") as mock_config_logger,
        patch("matrix_premid.__main__.acquire_lock") as mock_lock,
        patch("matrix_premid.__main__.os.path.exists", return_value=True),
        patch("matrix_premid.__main__.open", new_callable=MagicMock),
        patch(
            "matrix_premid.__main__.json.load",
            return_value={"accounts": [{"homeserver": "mock", "username": "@user"}]}
        ),
        patch("matrix_premid.__main__.keyring.get_password", return_value="mock_token"),
    ):

        mock_updater = AsyncMock()
        mock_updater_class.return_value = mock_updater
        mock_lock.return_value = MagicMock()

        # Mocking wait/gather to exit immediately
        with (
            patch("matrix_premid.__main__.asyncio.Event.wait", new_callable=AsyncMock),
            patch("matrix_premid.__main__.asyncio.gather", new_callable=AsyncMock),
        ):
            await main()

        mock_config_logger.assert_called_with(level=10)  # logging.DEBUG


@pytest.mark.asyncio
async def test_main_unset_flag():
    """Test the manual --unset flag in main."""
    with (
        patch("sys.argv", ["matrix_premid.py", "--unset"]),
        patch("matrix_premid.__main__.MatrixStatusUpdater") as mock_updater_class,
        patch("matrix_premid.__main__.os.path.exists", return_value=True),
        patch("matrix_premid.__main__.open", new_callable=MagicMock),
        patch(
            "matrix_premid.__main__.json.load",
            return_value={"accounts": [{"homeserver": "mock", "username": "@user"}]}
        ),
        patch("matrix_premid.__main__.keyring.get_password", return_value="mock_token"),
    ):
        mock_updater = AsyncMock()
        mock_updater_class.return_value = mock_updater

        await main()

        mock_updater.update.assert_awaited_with("", force=True, is_exit=True)
        mock_updater.close.assert_awaited()


@pytest.mark.asyncio
async def test_updater_update_lower_quality_ignored():
    """Test that lower quality metadata is ignored for the same song."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    updater.last_title = "Song"
    updater.last_quality = 20  # High quality
    await updater.update("Listening to: Song", title="Song")  # Low quality (10)
    assert updater._update_task is None


@pytest.mark.asyncio
async def test_updater_update_resets_idle_strikes():
    """Test that non-idle activity resets idle strikes."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    updater.idle_strikes = 5
    with patch.object(updater, "send_update", new_callable=AsyncMock):
        await updater.update("Listening to: Song")
        assert updater.idle_strikes == 0


@pytest.mark.asyncio
async def test_updater_update_debounces_idle():
    """Test that idle state is debounced before clearing status."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", idle_timeout=30, poll_interval=5)
    # 30/5 = 6 strikes needed
    for i in range(5):
        await updater.update("Idle", force=False)
        assert updater.idle_strikes == i + 1
        assert updater._update_task is None


@pytest.mark.asyncio
async def test_updater_update_cancels_existing_task():
    """Test that starting a new update cancels any pending debounced update."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    mock_task = MagicMock()
    mock_task.done.return_value = False
    updater._update_task = mock_task
    
    with patch.object(updater, "send_update", new_callable=AsyncMock):
        await updater.update("Listening to: Song")
        mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_updater_update_exit_sends_immediately():
    """Test that exiting sends the update immediately without debouncing."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    with patch.object(updater, "send_update", new_callable=AsyncMock) as mock_send:
        await updater.update("", is_exit=True)
        mock_send.assert_awaited_once()
        assert updater._update_task is None


@pytest.mark.asyncio
async def test_send_update_failure_logs_error(capsys):
    """Test that failed API requests log errors to stderr."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    updater.verbose = True
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text.return_value = "Bad Request"
        mock_session.put.return_value.__aenter__.return_value = mock_resp
        mock_get_session.return_value = mock_session

        await updater.send_update("Activity")
        
        _, err = capsys.readouterr()
        assert "presence failed (400): Bad Request" in err
        assert "account_data failed (400): Bad Request" in err


@pytest.mark.asyncio
async def test_send_update_exception_logs_debug(capsys):
    """Test that exceptions during API requests log to stderr if verbose."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok")
    updater.verbose = True
    with patch.object(updater, "_get_session", new_callable=AsyncMock) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("Crash")
        mock_get_session.return_value = mock_session

        await updater.send_update("Activity")
        
        _, err = capsys.readouterr()
        assert "presence error: Crash" in err
        assert "account_data error: Crash" in err


@pytest.mark.asyncio
@patch("matrix_premid.__main__.asyncio.create_subprocess_exec")
async def test_monitor_mpris_error_handling(mock_exec, capsys):
    """Test that monitor_mpris handles subprocess errors gracefully."""
    mock_exec.side_effect = OSError("Subprocess Error")
    
    # We need to break the infinite loop
    with patch("matrix_premid.__main__.asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
        try:
            await monitor_mpris([], 1)
        except asyncio.CancelledError:
            pass
            
    _, err = capsys.readouterr()
    assert "MPRIS Monitor Error: Subprocess Error" in err


@patch("matrix_premid.__main__.shutil.which", return_value="/usr/bin/matrix-premid")
@patch("matrix_premid.__main__.os.makedirs")
@patch("matrix_premid.__main__.open", new_callable=MagicMock)
@patch("matrix_premid.__main__.subprocess")
def test_install_service_flow(_mock_subprocess, _mock_open, _mock_makedirs, _mock_which):
    """Test the installation flow for systemd service."""
    install_service()
    assert _mock_makedirs.called
    assert _mock_open.called
    assert _mock_subprocess.run.called

