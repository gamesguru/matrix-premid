#!/usr/bin/env python3
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events and web-based updates.
"""

import asyncio
import os
import sys

from aiohttp import web
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
PORT = int(os.environ.get("PORT", 8080))


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
                print(f"✓ Status set: {activity}", flush=True)
            except (asyncio.TimeoutError, OSError) as e:
                print(f"ERROR: Matrix update failed: {e}", file=sys.stderr)


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
                data = out.decode("utf-8").strip()
                print(f"DEBUG: Initial fetch: {data}", flush=True)
                parts = data.split("|")
                if parts:
                    status = parts[0]
                    title = parts[1] if len(parts) > 1 else "Unknown Title"
                    artist = parts[2] if len(parts) > 2 else ""

                    if status == "Playing":
                        if title == "YouTube Music":
                            # This happens when the browser hasn't loaded metadata yet
                            activity = "Idle (YouTube Music)"
                        else:
                            activity = (
                                f"Listening to: {title} - {artist}"
                                if artist
                                else f"Watching: {title}"
                            )
                        await updater.update(activity)
                    else:
                        await updater.update("Idle")

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

                data = line.decode("utf-8").strip()
                if not data:
                    continue

                parts = data.split("|")
                if not parts or not parts[0]:
                    continue

                status = parts[0]
                title = parts[1] if len(parts) > 1 else "Unknown Title"
                artist = parts[2] if len(parts) > 2 else ""

                activity = "Idle"
                if status == "Playing":
                    if title == "YouTube Music":
                        activity = "Idle (YouTube Music)"
                    else:
                        activity = (
                            f"Listening to: {title} - {artist}"
                            if artist
                            else f"Watching: {title}"
                        )
                await updater.update(activity)

        except (asyncio.SubprocessError, OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(5)


async def handle_web_update(request):
    """Handle incoming POST updates from Tampermonkey."""
    try:
        data = await request.json()
        activity = data.get("activity")
        if activity:
            updater = request.app["updater"]
            await updater.update(activity)
            return web.Response(text="OK")
    except (web.HTTPException, ValueError):
        pass
    return web.Response(text="Error", status=400)


async def main():
    """Start the Matrix updater and web server."""
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(HOMESERVER, USERNAME, ACCESS_TOKEN, DEVICE_ID)
    print(f"Matrix User: {USERNAME} on {HOMESERVER}", flush=True)

    app = web.Application()
    app["updater"] = updater
    app.router.add_post("/update", handle_web_update)
    runner = web.AppRunner(app)
    await runner.setup()

    # Bind to an available port
    bound_port = None
    for port in [PORT, 8081, 8082, 8083, 8084, 8085]:
        try:
            site = web.TCPSite(runner, "localhost", port)
            await site.start()
            bound_port = port
            break
        except OSError:
            continue

    if not bound_port:
        print("ERROR: Could not bind to any port", file=sys.stderr)
        await updater.close()
        return

    print(f"Listening on port {bound_port} and MPRIS...", flush=True)

    async def keep_alive():
        """Periodically refresh presence to keep currently_active=True."""
        while True:
            if updater.last_activity:
                await updater.update(updater.last_activity, force=True)
            await asyncio.sleep(30)

    try:
        await asyncio.gather(
            monitor_mpris(updater),
            keep_alive(),
            asyncio.Event().wait(),  # Keep the web server running
        )
    except asyncio.CancelledError:
        pass
    finally:
        await updater.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
