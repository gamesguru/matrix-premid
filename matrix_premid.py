#!/usr/bin/env python3
import asyncio
import json
import os
import sys

from aiohttp import web
from dotenv import load_dotenv
from nio import AsyncClient
from nio.api import Api
from nio.responses import ErrorResponse

# Load environment variables from .env if present
load_dotenv()

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
PORT = int(os.environ.get("PORT", 8080))
# ---------------------


class MatrixStatusManager:
    def __init__(self, homeserver, username, access_token, device_id):
        self.client = AsyncClient(homeserver, username)
        self.client.access_token = access_token
        self.client.device_id = device_id
        self.client.user_id = username
        self.last_activity = ""
        self.lock = asyncio.Lock()

    async def close(self):
        await self.client.close()

    async def update_status(self, activity: str):
        if not activity:
            activity = "Idle"

        async with self.lock:
            if activity == self.last_activity:
                return

            print(f"Matrix Status -> {activity}")

            # 1. Update standard presence
            resp = await self.client.set_presence(
                presence="online", status_msg=activity
            )
            if isinstance(resp, ErrorResponse):
                print(f"ERROR: set_presence failed: {resp.message}", file=sys.stderr)
            else:
                # print("DEBUG: Presence updated successfully")
                pass

            # 2. Update Element's custom status (im.vector.user_status)
            content = {"status": activity} if activity != "Idle" else {}
            resp = await self.account_data_set("im.vector.user_status", content)
            if isinstance(resp, ErrorResponse):
                print(
                    f"ERROR: account_data_set failed: {resp.message}", file=sys.stderr
                )
            else:
                # print("DEBUG: Element status updated successfully")
                pass

            self.last_activity = activity

    async def account_data_set(self, event_type, content):
        """Set global account data for the user."""
        path = ["user", self.client.user_id, "account_data", event_type]
        query_parameters = {"access_token": self.client.access_token}
        full_path = Api._build_path(path, query_parameters)

        # _send is a private method but it's the only way to send custom PUT
        from nio.responses import EmptyResponse

        return await self.client._send(
            EmptyResponse, "PUT", full_path, data=Api.to_json(content)
        )


async def monitor_mpris(manager: MatrixStatusManager):
    """
    Hooks into the D-Bus MPRIS interface via playerctl.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "playerctl",
            "metadata",
            "--format",
            "{{status}}|{{title}}|{{artist}}",
            "--follow",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(
            "ERROR: 'playerctl' is not installed or not in PATH.",
            file=sys.stderr,
        )
        return

    while True:
        line = await process.stdout.readline()
        if not line:
            break

        data = line.decode("utf-8").strip()
        if not data:
            continue

        try:
            status, title, artist = data.split("|", 2)
        except ValueError:
            continue

        activity = "Idle"
        if status == "Playing":
            if artist:
                activity = f"Listening to: {title} - {artist}"
            else:
                activity = f"Watching: {title}"

        await manager.update_status(activity)


async def handle_web_update(request):
    """Handle POST /update from Tampermonkey script."""
    try:
        data = await request.json()
        activity = data.get("activity")
        if activity:
            manager = request.app["manager"]
            await manager.update_status(activity)
            return web.Response(text="Updated")
    except Exception as e:
        print(f"Web update error: {e}", file=sys.stderr)

    return web.Response(text="Failed", status=400)


async def main():
    if not HOMESERVER or not USERNAME or not ACCESS_TOKEN:
        print(
            "ERROR: Missing required configuration in environment variables or .env",
            file=sys.stderr,
        )
        sys.exit(1)

    manager = MatrixStatusManager(HOMESERVER, USERNAME, ACCESS_TOKEN, DEVICE_ID)
    print(f"Matrix User: {USERNAME} on {HOMESERVER}")
    print(f"Listening for MPRIS events and web updates on port {PORT}...")

    # Set up web server
    app = web.Application()
    app["manager"] = manager
    app.router.add_post("/update", handle_web_update)
    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "localhost", PORT)
        await site.start()
    except OSError as e:
        if e.errno == 98:
            print(
                f"ERROR: Port {PORT} is already in use. Is another instance running?",
                file=sys.stderr,
            )
            await manager.close()
            await runner.cleanup()
            sys.exit(1)
        else:
            raise

    try:
        # Run MPRIS monitor
        await monitor_mpris(manager)
    except asyncio.CancelledError:
        pass
    finally:
        await manager.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
