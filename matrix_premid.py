#!/usr/bin/env python3
"""Matrix Presence Updater - Simple & Functional."""

import asyncio
import os
import sys

from aiohttp import web
from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, PresenceSetResponse

load_dotenv()

# Configuration
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))


class State:
    """Shared state for the updater."""

    last_activity = ""
    client = None


async def update_status(activity):
    """Update Matrix presence and Element custom status."""
    if activity == State.last_activity:
        return
    print(f"Matrix Status -> {activity}", flush=True)

    try:
        # 1. Standard Presence with currently_active=True
        path = ["presence", State.client.user_id, "status"]
        full_path = Api._build_path(path, {"access_token": State.client.access_token})
        await State.client._send(
            PresenceSetResponse,
            "PUT",
            full_path,
            data=Api.to_json(
                {"presence": "online", "status_msg": activity, "currently_active": True}
            ),
        )

        # 2. Element Custom Status (im.vector.user_status)
        path = ["user", State.client.user_id, "account_data", "im.vector.user_status"]
        full_path = Api._build_path(path, {"access_token": State.client.access_token})
        content = {"status": activity} if activity != "Idle" else {}
        await State.client._send(
            EmptyResponse, "PUT", full_path, data=Api.to_json(content)
        )

        State.last_activity = activity
        print(f"✓ Updated: {activity}", flush=True)
    except Exception as e:
        print(f"Update Error: {e}", file=sys.stderr)


async def monitor_mpris():
    """Continuously monitor MPRIS via playerctl."""
    while True:
        try:
            # Fetch current status first
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
                s, t, a = out.decode().strip().split("|", 2)
                if s == "Playing":
                    await update_status(
                        f"Listening to: {t} - {a}" if a else f"Watching: {t}"
                    )
                else:
                    await update_status("Idle")

            # Then follow for changes
            proc = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}",
                "--follow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                s, t, a = line.decode().strip().split("|", 2)
                if s == "Playing":
                    await update_status(
                        f"Listening to: {t} - {a}" if a else f"Watching: {t}"
                    )
                else:
                    await update_status("Idle")
        except Exception:
            pass
        await asyncio.sleep(5)


async def handle_web(request):
    """Handle status updates from Tampermonkey."""
    try:
        data = await request.json()
        if "activity" in data:
            print(f"Web update: {data['activity']}", flush=True)
            await update_status(data["activity"])
        return web.Response(text="OK")
    except Exception:
        return web.Response(text="Error", status=400)


async def main():
    """Initialize client and run tasks."""
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    State.client = AsyncClient(HOMESERVER, USERNAME)
    State.client.access_token = ACCESS_TOKEN
    State.client.user_id = USERNAME

    app = web.Application()
    app.router.add_post("/update", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()

    # Simple port fallback
    for p in [PORT, 8081, 8082]:
        try:
            await web.TCPSite(runner, "localhost", p).start()
            print(f"Listening on port {p}", flush=True)
            break
        except OSError:
            continue

    print(f"Matrix: {USERNAME} | {HOMESERVER}", flush=True)
    await monitor_mpris()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
