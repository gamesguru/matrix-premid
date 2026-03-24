"""Tests for MatrixStatusUpdater and monitor_mpris."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from matrix_premid import MatrixStatusUpdater, main, monitor_mpris


def test_updater_init():
    """Test the updater initializes the nio client correctly."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    assert updater.client.user_id == "@test:mock"
    assert updater.client.device_id == "dev"


@pytest.mark.asyncio
async def test_updater_update():
    """Test pushing presence state to the Matrix room."""
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    updater.client.room_put_state = AsyncMock()

    await updater.update("Listening to: Song")
    updater.client.room_put_state.assert_awaited()


@pytest.mark.asyncio
@patch("matrix_premid.asyncio.create_subprocess_exec")
async def test_monitor_mpris_picks_best_activity(mock_exec):
    """Test the main daemon loop processes playerctl output and updates status."""
    mock_proc = AsyncMock()
    # Provide one valid output then simulate exception to break the infinite loop
    mock_proc.communicate.side_effect = [
        (b"Playing|Awesome Song|Awesome Artist|firefox\n", b""),
        Exception("Break loop"),
    ]
    mock_exec.return_value = mock_proc

    updater = AsyncMock()
    try:
        await monitor_mpris(updater)
    except Exception:
        pass

    updater.update.assert_awaited_with("Listening to: Awesome Song - Awesome Artist")


@pytest.mark.asyncio
@patch("matrix_premid.sys.exit")
@patch("matrix_premid.asyncio.gather")
@patch.dict(
    "os.environ",
    {
        "HOMESERVER": "mock",
        "USERNAME": "@user",
        "ACCESS_TOKEN": "tok",
        "DEVICE_ID": "dev",
    },
)
async def test_main_execution_mocked_gather(mock_gather, mock_exit):
    """Test the main entrypoint bootstraps the updater and yields to gather."""
    # We just need to assert main invokes gather without hanging
    await main()
    mock_gather.assert_awaited()


@pytest.mark.asyncio
async def test_updater_close():
    updater = MatrixStatusUpdater("http://mock", "@test:mock", "tok", "dev")
    updater.client.close = AsyncMock()
    await updater.close()
    updater.client.close.assert_awaited()
