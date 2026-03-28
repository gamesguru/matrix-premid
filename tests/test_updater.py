"""Tests for MatrixStatusUpdater and monitor_mpris."""

# pylint: disable=protected-access,no-member,redefined-outer-name,broad-exception-caught

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from matrix_premid.__main__ import (
    SEP_STR,
    MatrixStatusUpdater,
    install_service,
    main,
    monitor_mpris,
)


@pytest.fixture(autouse=True)
def patch_sleep():
    """Bypass asyncio.sleep delays globally for fast test execution."""
    with patch("asyncio.sleep", new_callable=AsyncMock) as m:
        yield m


@pytest_asyncio.fixture
async def matrix_updater_obj():
    """Fixture to provide a MatrixStatusUpdater and ensure it's closed."""
    u = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    yield u
    if u._update_task and not u._update_task.done():
        u._update_task.cancel()
        try:
            await u._update_task
        except (asyncio.CancelledError, Exception):
            pass
    await u.close()


def test_updater_init():
    """Test the updater initializes correctly."""
    u = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    assert u.homeserver == "http://mock"
    assert u.username == "@test:mock"
    assert u.access_token == "tok"
    assert u.device_id == "dev"


@pytest.mark.asyncio
async def test_updater_update(matrix_updater_obj):
    """Test pushing presence state to the Matrix room."""
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="OK")
        mock_session.put.return_value.__aenter__.return_value = mock_resp
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.update("Listening to: Song | YT Music")
        await matrix_updater_obj._update_task

        # Verify Presence Update was called via aiohttp
        mock_session.put.assert_any_call(
            "http://mock/_matrix/client/v3/presence/@test:mock/status",
            json=ANY,
            headers=ANY,
            timeout=ANY,
        )


@pytest.mark.asyncio
async def test_updater_update_paused(matrix_updater_obj):
    """Test a paused song yields presence correctly."""
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="OK")
        mock_session.put.return_value.__aenter__.return_value = mock_resp
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.update("Paused: Song - Artist | YT Music")
        await matrix_updater_obj._update_task
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_empty(matrix_updater_obj):
    """Test empty string is ignored by default (new behavior)."""
    await matrix_updater_obj.update("")
    assert matrix_updater_obj._update_task is None


@pytest.mark.asyncio
async def test_updater_update_other(matrix_updater_obj):
    """Test non-music activities receive correct base quality attributes."""
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="OK")
        mock_session.put.return_value.__aenter__.return_value = mock_resp
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.update("Watching: Movie", title="Movie")
        await matrix_updater_obj._update_task
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_exception(matrix_updater_obj):
    """Test updating surviving network errors."""
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("Network Error")
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.update("Listening to: Song")
        await matrix_updater_obj._update_task
        # Should not crash despite the network error
        assert mock_session.put.call_count >= 1


@pytest.mark.asyncio
async def test_updater_update_same_song_ignored(matrix_updater_obj):
    """Test ignoring unchanged song status strings."""
    matrix_updater_obj.last_activity = "Listening to: Song"
    matrix_updater_obj.last_title = "Song"
    await matrix_updater_obj.update("Listening to: Song", title="Song")
    assert matrix_updater_obj._update_task is None


@pytest.mark.asyncio
@patch("matrix_premid.__main__.asyncio.create_subprocess_exec")
async def test_monitor_mpris_picks_best_activity(mock_exec, matrix_updater_obj):
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

    matrix_updater_obj.update = AsyncMock()
    try:
        await monitor_mpris([matrix_updater_obj], 5)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    matrix_updater_obj.update.assert_awaited_with(
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
    u = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    u._session = MagicMock()
    u._session.closed = False
    u._session.close = AsyncMock()

    await u.close()
    u._session.close.assert_awaited()


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
async def test_updater_update_lower_quality_ignored(matrix_updater_obj):
    """Test that lower quality metadata is ignored for the same song."""
    matrix_updater_obj.last_title = "Song"
    matrix_updater_obj.last_quality = 20  # High quality
    await matrix_updater_obj.update("Listening to: Song", title="Song")  # Low quality (10)
    assert matrix_updater_obj._update_task is None


@pytest.mark.asyncio
async def test_updater_update_resets_idle_strikes(matrix_updater_obj):
    """Test that non-idle activity resets idle strikes."""
    matrix_updater_obj.idle_strikes = 5
    with patch.object(matrix_updater_obj, "send_update", new_callable=AsyncMock):
        await matrix_updater_obj.update("Listening to: Song")
        assert matrix_updater_obj.idle_strikes == 0


@pytest.mark.asyncio
async def test_updater_update_cancels_existing_task(matrix_updater_obj):
    """Test that starting a new update cancels any pending debounced update."""
    mock_task = MagicMock()
    mock_task.done.return_value = False
    matrix_updater_obj._update_task = mock_task

    with patch.object(matrix_updater_obj, "send_update", new_callable=AsyncMock):
        await matrix_updater_obj.update("Listening to: Song")
        mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_updater_update_exit_sends_immediately(matrix_updater_obj):
    """Test that exiting sends the update immediately without debouncing."""
    with patch.object(matrix_updater_obj, "send_update", new_callable=AsyncMock) as mock_send:
        await matrix_updater_obj.update("", is_exit=True)
        mock_send.assert_awaited_once()
        assert matrix_updater_obj._update_task is None


@pytest.mark.asyncio
async def test_send_update_failure_logs_error(matrix_updater_obj, capsys):
    """Test that failed API requests log errors to stderr."""
    matrix_updater_obj.verbose = True
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text.return_value = "Bad Request"
        mock_session.put.return_value.__aenter__.return_value = mock_resp
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.send_update("Activity")

        _, err = capsys.readouterr()
        assert "presence failed (400): Bad Request" in err
        assert "account_data failed (400): Bad Request" in err


@pytest.mark.asyncio
async def test_send_update_exception_logs_debug(matrix_updater_obj, capsys):
    """Test that exceptions during API requests log to stderr if verbose."""
    matrix_updater_obj.verbose = True
    with patch.object(
        matrix_updater_obj, "_get_session", new_callable=AsyncMock
    ) as mock_get_session:
        mock_session = MagicMock()
        mock_session.put.side_effect = Exception("Crash")
        mock_get_session.return_value = mock_session

        await matrix_updater_obj.send_update("Activity")

        _, err = capsys.readouterr()
        assert "presence error: Crash" in err
        assert "account_data error: Crash" in err


@pytest.mark.asyncio
@patch("matrix_premid.__main__.asyncio.create_subprocess_exec")
async def test_monitor_mpris_error_handling(mock_exec, capsys):
    """Test that monitor_mpris handles subprocess errors gracefully."""
    mock_exec.side_effect = OSError("Subprocess Error")

    # We need to break the infinite loop
    with patch(
        "matrix_premid.__main__.asyncio.sleep",
        side_effect=[None, asyncio.CancelledError()],
    ):
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
def test_install_service_flow(
    _mock_subprocess, _mock_open, _mock_makedirs, _mock_which
):
    """Test the installation flow for systemd service."""
    install_service()
    assert _mock_makedirs.called
    assert _mock_open.called
    assert _mock_subprocess.run.called
