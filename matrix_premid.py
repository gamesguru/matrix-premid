#!/usr/bin/env python3
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, PresenceSetResponse

# Load environment variables from .env if present
load_dotenv()

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
# ---------------------


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
            if not force and activity == self.last_activity:
                return

            print(f"Matrix Status -> {activity}", flush=True)

            try:
                # 1. Update standard presence (with currently_active=True)
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

    if artist:
        return f"Listening to: {title} - {artist}"

    return f"Watching: {title}"


async def monitor_mpris(updater: MatrixStatusUpdater):
    """Monitor MPRIS events via playerctl."""
    while True:
        try:
            # Initial fetch
            proc = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await proc.communicate()
            if out:
                raw = out.decode("utf-8").strip()
                await updater.update(parse_mpris_data(raw))

            # Follow mode
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
                    await updater.update(parse_mpris_data(raw))

        except asyncio.CancelledError:
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(5)


async def main():
    """Start the Matrix updater."""
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(HOMESERVER, USERNAME, ACCESS_TOKEN, DEVICE_ID)
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
        await asyncio.gather(
            monitor_mpris(updater),
            keep_alive(),
        )
    except asyncio.CancelledError:
        pass
    finally:
        print("\nShutting down gracefully...", flush=True)
        await updater.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
