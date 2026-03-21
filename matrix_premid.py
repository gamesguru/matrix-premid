#!/usr/bin/env python3
import asyncio
import os
import sys

from nio import AsyncClient

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
        print(
            "Please install via your package manager (pacman -S playerctl)",
            file=sys.stderr,
        )
        sys.exit(1)

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
    if not HOMESERVER or not USERNAME or not ACCESS_TOKEN:
        print(
            "ERROR: Missing required configuration in environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = AsyncClient(HOMESERVER, USERNAME)
    client.access_token = ACCESS_TOKEN
    client.device_id = DEVICE_ID
    client.user_id = USERNAME

    last_activity = ""
    print("Listening for native Linux MPRIS D-Bus events...")

    backoff = 1
    max_backoff = 300

    while True:
        try:
            # Loop over the async generator as D-Bus events stream in
            async for current_activity in monitor_mpris():
                # Reset backoff on successful connection/event
                backoff = 1

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
                        # Clear custom status text if nothing is playing
                        await client.set_account_data(
                            "im.vector.user_status", {}
                        )  # noqa: E501
                    else:
                        await client.set_account_data(
                            "im.vector.user_status",
                            {"status": current_activity},
                        )

                    last_activity = current_activity

        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Connection or MPRIS error: {e}", file=sys.stderr)
            print(f"Retrying in {backoff} seconds...", file=sys.stderr)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
        except KeyboardInterrupt:
            print("\nStopping...")
            break

    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
