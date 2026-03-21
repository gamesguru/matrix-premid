#!/usr/bin/env python3
import asyncio
import os

from dotenv import load_dotenv
from nio import AsyncClient

load_dotenv()

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
# ---------------------


async def monitor_mpris():
    """
    Hooks into the D-Bus MPRIS interface via playerctl.
    Yields the formatted activity string instantly when media state changes.
    """
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

        try:
            status, title, artist = data.split("|", 2)
        except ValueError:
            continue

        if status == "Playing":
            # Format cleanly depending on whether an artist is provided
            if artist:
                yield f"Listening to: {title} - {artist}"
            else:
                yield f"Watching: {title}"
        else:
            yield "Idle"


async def main():
    """Main method of script/module."""
    client = AsyncClient(HOMESERVER, USERNAME)
    client.access_token = ACCESS_TOKEN
    client.device_id = DEVICE_ID
    client.user_id = USERNAME

    last_activity = ""
    print("Listening for native Linux MPRIS D-Bus events...")

    try:
        # Loop over the async generator as D-Bus events stream in
        async for current_activity in monitor_mpris():

            if current_activity != last_activity:
                print(f"Matrix Status -> {current_activity}")

                # 0. Ping the sync endpoint to reset the idle timer
                await client.sync(timeout=0, set_presence="online")

                # 1. Update standard presence
                await client.set_presence(
                    presence="online", status_msg=current_activity
                )

                # 2. Update Element's custom status
                if current_activity == "Idle":
                    # Clear the custom status text if nothing is playing
                    await client.set_account_data("im.vector.user_status", {})
                else:
                    await client.set_account_data(
                        "im.vector.user_status", {"status": current_activity}
                    )

                last_activity = current_activity

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
