#!/usr/bin/env python3
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events.
"""

import asyncio
import fcntl
import logging
import os
import sys

from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, PresenceSetResponse

# Load environment variables from .env if present
load_dotenv()

# Lock file to prevent multiple instances
LOCK_FILE = "/tmp/matrix-premid.lock"


def acquire_lock():
    """Ensure only one instance runs. Returns the file descriptor."""
    try:
        # We need to keep the file open for the duration of the process
        # pylint: disable=consider-using-with
        lock_fd = open(LOCK_FILE, "w", encoding="utf-8")
        fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return lock_fd
    except OSError:
        print("ERROR: Another instance is already running.", file=sys.stderr)
        sys.exit(1)


# Suppress noisy matrix-nio validation errors
logging.getLogger("nio").setLevel(logging.CRITICAL)

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")


class MatrixStatusUpdater:
    """Manages Matrix status updates with state tracking and error handling."""

    def __init__(self, homeserver, username, access_token, device_id=None):
        self.client = AsyncClient(homeserver, username)
        self.client.access_token = access_token
        self.client.user_id = username
        if device_id:
            self.client.device_id = device_id

        self.last_activity = ""
        self.lock = asyncio.Lock()

    async def close(self):
        """Close the Matrix client session."""
        await self.client.close()

    async def update(self, activity: str, force: bool = False):
        """Update both standard presence and Element custom status."""
        if not activity:
            activity = "Idle"

        async with self.lock:
            # Only log if it's a real transition to a new status
            is_new = activity != self.last_activity
            if not force and not is_new:
                return

            if is_new:
                print(f"Matrix Status -> {activity}", flush=True)
                # Update state immediately to prevent race conditions
                self.last_activity = activity

            try:
                # 1. Update standard presence (explicit online + text)
                await self.client.set_presence(presence="online", status_msg=activity)

                path = ["presence", self.client.user_id, "status"]
                # pylint: disable=protected-access
                full_path = Api._build_path(
                    path, {"access_token": self.client.access_token}
                )

                # Send custom PUT to ensure currently_active is True
                await self.client._send(
                    PresenceSetResponse,
                    "PUT",
                    full_path,
                    data=Api.to_json(
                        {
                            "presence": "online",
                            "status_msg": activity,
                            "currently_active": True,
                        }
                    ),
                )

                # 2. Update Element's custom status (im.vector.user_status)
                path = [
                    "user",
                    self.client.user_id,
                    "account_data",
                    "im.vector.user_status",
                ]
                # pylint: disable=protected-access
                full_path = Api._build_path(
                    path, {"access_token": self.client.access_token}
                )
                content = {"status": activity} if activity != "Idle" else {}
                await self.client._send(
                    EmptyResponse, "PUT", full_path, data=Api.to_json(content)
                )

            except (asyncio.TimeoutError, OSError) as e:
                print(f"ERROR: Matrix update failed: {e}", file=sys.stderr)


def parse_mpris_data(data: str) -> str:
    """Parse raw playerctl data into a clean activity string."""
    # Filter out empty strings from trailing pipes
    parts = [p.strip() for p in data.split("|") if p.strip()]
    if not parts or not parts[0]:
        return "Idle"

    status = parts[0]
    title = parts[1] if len(parts) > 1 else "Unknown Title"
    artist = parts[2] if len(parts) > 2 else ""
    player = parts[3].lower() if len(parts) > 3 else ""

    if status != "Playing":
        return "Idle"

    # Aggressive YT Music detection
    is_ytm = (
        "YouTube Music" in data
        or "plasma-browser-integration" in player
        or "firefox" in player
        or "chrome" in player
    )

    if title == "YouTube Music" and not artist:
        return "Idle (YouTube Music)"

    # Filter out generic 'YouTube Music' artist placeholder
    clean_artist = "" if artist == "YouTube Music" else artist

    if clean_artist:
        activity = f"Listening to: {title} - {clean_artist}"
    else:
        activity = f"Watching: {title}"

    # Append ideal footer
    if is_ytm and "YT Music" not in activity:
        activity += " | YT Music"

    return activity


async def monitor_mpris(updater: MatrixStatusUpdater):
    """Monitor MPRIS events via playerctl."""
    debounce_task = None
    state = {"pending": ""}

    async def do_debounced_update(activity):
        # Longer wait for low-confidence metadata
        if activity == "Idle" or activity.startswith("Watching:"):
            delay = 2.0
        else:
            delay = 0.4

        await asyncio.sleep(delay)

        # If a better update arrived while we were waiting, skip this one
        if activity.startswith("Watching:") and state["pending"].startswith(
            "Listening:"
        ):
            return

        await updater.update(activity)

    while True:
        try:
            # Follow mode
            process = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}|{{playerName}}",
                "--follow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                raw = line.decode("utf-8").strip()
                if raw:
                    print(f"DEBUG: Raw data: {raw}", flush=True)
                    activity = parse_mpris_data(raw)
                    state["pending"] = activity
                    if debounce_task:
                        debounce_task.cancel()
                    debounce_task = asyncio.create_task(do_debounced_update(activity))

        except asyncio.CancelledError:
            if debounce_task:
                debounce_task.cancel()
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(5)


async def main():
    """Start the Matrix updater."""
    # We must keep the lock_fd in scope to prevent the lock from being released
    lock_fd = acquire_lock()  # pylint: disable=unused-variable # noqa: F841
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(HOMESERVER, USERNAME, ACCESS_TOKEN)
    print(f"Matrix User: {USERNAME} on {HOMESERVER}", flush=True)

    async def keep_alive():
        """Periodically refresh presence and sync to keep status active."""
        while True:
            try:
                # LONG sync is necessary to stay 'online'
                await asyncio.gather(
                    updater.client.sync(timeout=30, set_presence="online"),
                    updater.update(updater.last_activity, force=True),
                )
            except asyncio.CancelledError:
                break
            except (asyncio.TimeoutError, OSError):
                await asyncio.sleep(5)

    try:
        print("Listening for MPRIS events...", flush=True)
        await asyncio.gather(
            monitor_mpris(updater),
            keep_alive(),
        )
    except asyncio.CancelledError:
        pass
    finally:
        await updater.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
