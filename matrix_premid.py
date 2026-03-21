#!/usr/bin/env python3
import asyncio
import os

from aiohttp import web
from dotenv import load_dotenv
from nio import AsyncClient

load_dotenv()

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
# ---------------------


client = AsyncClient(HOMESERVER, USERNAME)
client.access_token = ACCESS_TOKEN
client.device_id = DEVICE_ID
client.user_id = USERNAME


async def handle_update(request):
    """Main handler."""

    last_activity = ""

    try:
        data = await request.json()
        current_activity = data.get("activity", "Idle")

        if current_activity != last_activity:
            print(f"Attempting to set Matrix Status -> {current_activity}")

            # Ping the sync endpoint to reset the server's idle timer to 0
            # await client.sync(timeout=0, set_presence="online")

            # Update standard presence and capture response
            pres_resp = await client.set_presence(
                presence="online", status_msg=current_activity
            )
            print(f"Presence API Response: {pres_resp}")

            # Update Element's custom status and capture response
            acc_resp = await client.set_account_data(
                "im.vector.user_status", {"status": current_activity}
            )
            print(f"Account Data API Response: {acc_resp}")

            last_activity = current_activity

        return web.Response(text="Success")
    except Exception as e:
        return web.Response(status=400, text=str(e))


async def main():
    """Main method of script/module."""

    # Setup the web server
    app = web.Application()
    app.router.add_post("/update", handle_update)
    runner = web.AppRunner(app)
    await runner.setup()

    # Run on port 8080
    site = web.TCPSite(runner, "localhost", 8080)
    await site.start()
    print("Listening for browser updates on http://localhost:8080/update")

    try:
        # Keep the script running
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await runner.cleanup()
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
