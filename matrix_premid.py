#!/usr/bin/env python3
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events.
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, PresenceSetResponse

# Load environment variables from .env if present
load_dotenv()

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

            try:
                # 1. Update standard presence
                await self.client.set_presence(presence="online", status_msg=activity)

                path = ["presence", self.client.user_id, "status"]
                # pylint: disable=protected-access
                full_path = Api._build_path(
                    path, {"access_token": self.client.access_token}
                )

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

                self.last_activity = activity
            except (asyncio.TimeoutError, OSError) as e:
                print(f"ERROR: Matrix update failed: {e}", file=sys.stderr)


def parse_mpris_data(data: str) -> str:
    """Parse raw playerctl data into a clean activity string."""
    parts = [p.strip() for p in data.split("|")]
    if not parts or not parts[0]:
        return "Idle"

    status = parts[0]
    title = parts[1] if len(parts) > 1 else "Unknown Title"
    artist = parts[2] if len(parts) > 2 else ""

    if status != "Playing":
        return "Idle"

    # 'YouTube Music' as an artist is usually a placeholder before metadata loads
    clean_artist = "" if artist == "YouTube Music" else artist

    if clean_artist:
        return f"Listening to: {title} - {clean_artist}"

    return f"Watching: {title}"


async def monitor_mpris(updater: MatrixStatusUpdater):
    """Monitor MPRIS events via playerctl."""
    debounce_task = None
    pending_activity = ""

    async def do_debounced_update(activity):
        nonlocal pending_activity
        # Wait longer for 'Watching' as it's almost always followed by 'Listening'
        delay = 1.5 if activity.startswith("Watching:") else 0.2
        await asyncio.sleep(delay)

        # If a better update arrived while we were waiting, skip this one
        if activity.startswith("Watching:") and pending_activity.startswith(
            "Listening:"
        ):
            return

        await updater.update(activity)

    while True:
        try:
            # Follow mode (usually emits current state on start anyway)
            process = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}",
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
                    activity = parse_mpris_data(raw)
                    pending_activity = activity
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
        # Simple port fallback logic removed since web server is gone
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
