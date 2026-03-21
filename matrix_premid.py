#!/usr/bin/env python3
"""Matrix Presence Updater script."""

import asyncio
import os
import sys

from aiohttp import web
from dotenv import load_dotenv
from nio import AsyncClient
from nio.api import Api
from nio.responses import EmptyResponse, ErrorResponse, PresenceSetResponse

# Load environment variables
load_dotenv()

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
PORT = int(os.environ.get("PORT", 8080))
# ---------------------


async def account_data_set(client, event_type, content):
    """Set global account data for the user."""
    path = ["user", client.user_id, "account_data", event_type]
    query_parameters = {"access_token": client.access_token}
    # pylint: disable=protected-access
    full_path = Api._build_path(path, query_parameters)

    return await client._send(
        EmptyResponse, "PUT", full_path, data=Api.to_json(content)
    )


async def set_presence_custom(client, presence, status_msg=None):
    """Custom presence update that supports currently_active flag."""
    path = ["presence", client.user_id, "status"]
    query_parameters = {"access_token": client.access_token}
    # pylint: disable=protected-access
    full_path = Api._build_path(path, query_parameters)

    content = {"presence": presence}
    if status_msg:
        content["status_msg"] = status_msg
    content["currently_active"] = True

    return await client._send(
        PresenceSetResponse, "PUT", full_path, data=Api.to_json(content)
    )


async def update_matrix_status(client, activity, last_activity):
    """Send status updates to Matrix if they have changed."""
    if activity == last_activity:
        print(f"Status unchanged: {activity}")
        return last_activity

    print(f"Matrix Status -> {activity}")

    # 1. Update standard presence
    resp = await set_presence_custom(client, "online", activity)
    if isinstance(resp, ErrorResponse):
        print(f"ERROR: set_presence failed: {resp.message}", file=sys.stderr)
    else:
        print(f"✓ Presence updated: {activity}")

    # 2. Update Element's custom status
    content = {"status": activity} if activity != "Idle" else {}
    resp = await account_data_set(client, "im.vector.user_status", content)
    if isinstance(resp, ErrorResponse):
        print(f"ERROR: account_data_set failed: {resp.message}", file=sys.stderr)
    else:
        print(f"✓ Custom status updated: {activity}")

    return activity


async def monitor_mpris(client, app):
    """Monitor MPRIS and update app state."""
    while True:
        try:
            # 1. Fetch current status once on startup
            print("Fetching initial player status...", flush=True)
            init_proc = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            init_out, _ = await init_proc.communicate()
            if init_out:
                data = init_out.decode("utf-8").strip()
                print(f"DEBUG: Initial status: {data}", flush=True)
                try:
                    status, title, artist = data.split("|", 2)
                    activity = "Idle"
                    if status == "Playing":
                        activity = (
                            f"Listening to: {title} - {artist}"
                            if artist
                            else f"Watching: {title}"
                        )
                    app["last_activity"] = await update_matrix_status(
                        client, activity, app["last_activity"]
                    )
                except ValueError:
                    pass

            # 2. Start follow mode
            print("Starting playerctl monitor...", flush=True)
            process = await asyncio.create_subprocess_exec(
                "playerctl",
                "metadata",
                "--format",
                "{{status}}|{{title}}|{{artist}}",
                "--follow",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Task to read stderr
            async def log_stderr(stderr):
                while True:
                    err_line = await stderr.readline()
                    if not err_line:
                        break
                    print(f"playerctl stderr: {err_line.decode().strip()}", flush=True)

            asyncio.create_task(log_stderr(process.stderr))

            while True:
                line = await process.stdout.readline()
                if not line:
                    print(f"playerctl process exited with code {process.returncode}", flush=True)
                    break
                data = line.decode("utf-8").strip()
                if not data:
                    continue

                print(f"DEBUG: playerctl raw: {data}", flush=True)

                try:
                    status, title, artist = data.split("|", 2)
                    activity = "Idle"
                    if status == "Playing":
                        activity = (
                            f"Listening to: {title} - {artist}"
                            if artist
                            else f"Watching: {title}"
                        )

                    app["last_activity"] = await update_matrix_status(
                        client, activity, app["last_activity"]
                    )
                except ValueError:
                    continue
        # pylint: disable=broad-exception-caught
        except Exception as e:
            print(f"MPRIS Error: {e}", file=sys.stderr)
        await asyncio.sleep(5)


async def handle_web_update(request):
    """Handle status updates from the web endpoint."""
    try:
        data = await request.json()
    except Exception as e:
        print(f"Web update JSON error: {e}")
        return web.Response(text="Invalid JSON", status=400)

    activity = data.get("activity")
    print(f"Web update received: {activity}")
    if activity:
        client = request.app["client"]
        request.app["last_activity"] = await update_matrix_status(
            client, activity, request.app["last_activity"]
        )
        return web.Response(text="Updated")
    return web.Response(text="Failed", status=400)


async def main():
    """Main entry point for the Presence Updater."""
    if not HOMESERVER or not USERNAME or not ACCESS_TOKEN:
        print("ERROR: Missing configuration", file=sys.stderr)
        sys.exit(1)

    client = AsyncClient(HOMESERVER, USERNAME)
    client.access_token = ACCESS_TOKEN
    client.user_id = USERNAME

    app = web.Application()
    app["client"] = client
    app["last_activity"] = ""
    app.router.add_post("/update", handle_web_update)

    runner = web.AppRunner(app)
    await runner.setup()

    # Try ports
    actual_port = None
    for port in [PORT] + list(range(8081, 8090)):
        try:
            site = web.TCPSite(runner, "localhost", port)
            await site.start()
            actual_port = port
            break
        except OSError:
            continue

    if not actual_port:
        print("ERROR: No ports available", file=sys.stderr)
        return

    print(f"Matrix User: {USERNAME} on {HOMESERVER}")
    print(f"Listening on port {actual_port} and MPRIS...")

    try:
        # Run everything concurrently
        await asyncio.gather(
            monitor_mpris(client, app), asyncio.Event().wait()  # Keep web server alive
        )
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
