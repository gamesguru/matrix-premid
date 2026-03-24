#!/usr/bin/env python3
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events.
"""

import asyncio
import fcntl
import html
import logging
import os
import sys

from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, PresenceSetResponse

PROVIDERS = {
    "youtube music": "YouTube Music",
    "yt music": "YouTube Music",
    "music.youtube.com": "YouTube Music",
    "spotify": "Spotify",
    "netflix": "Netflix",
    "plex": "Plex",
    "soundcloud": "SoundCloud",
    "last.fm": "Last.fm",
    "twitch": "Twitch",
    "apple music": "Apple Music",
}

SEP_STR = "_||_"

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
        self.last_title = ""
        self.last_quality = 0  # 0: Idle, 1: Basic, 2: Full (Artist)
        self.lock = asyncio.Lock()

    async def close(self):
        """Close the Matrix client session."""
        await self.client.close()

    async def update(self, activity: str, title: str = "", force: bool = False):
        """Update Matrix presence with metadata quality filtering."""
        if not activity:
            activity = "Idle"

        # Determine metadata quality
        quality = 0
        if activity.startswith("Listening to:"):
            quality = 20 if " - " in activity else 10
            if "YT Music" in activity:
                quality += 1
        elif activity.startswith("Paused:"):
            quality = 6 if " - " in activity else 4
            if "YT Music" in activity:
                quality += 1
        elif activity != "Idle" and not activity.startswith("Idle"):
            quality = 10

        async with self.lock:
            # If same song but lower quality metadata, ignore it
            # This prevents Firefox (no artist) from overriding Plasma (artist)
            if not force and title and title == self.last_title:
                if quality < self.last_quality:
                    return

            is_new = activity != self.last_activity
            if not force and not is_new:
                return

            if is_new:
                print(f"Matrix Status -> {activity}", flush=True)
                self.last_activity = activity
                self.last_title = title
                self.last_quality = quality

            try:
                # 1. Standard Presence
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

                # 2. Element Custom Status
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


def parse_mpris_data(data: str, global_provider: str = "") -> tuple[str, str]:
    """Parse playerctl data into (activity_string, normalized_title)."""
    data = html.unescape(data)
    parts = [p.strip() for p in data.split(SEP_STR)]
    if not parts or not parts[0]:
        return "Idle", ""

    status = parts[0]
    title = parts[1] if len(parts) > 1 else "Unknown Title"
    artist = parts[2] if len(parts) > 2 else ""

    if status not in ("Playing", "Paused"):
        return "Idle", ""

    norm_title = title.strip()

    for provider in set(PROVIDERS.values()):
        for suffix in [
            f" - {provider}",
            f" | {provider}",
            f" - {provider.lower()}",
            f" | {provider.lower()}",
        ]:
            if norm_title.endswith(suffix):
                norm_title = norm_title[: -len(suffix)].strip()
            if artist.endswith(suffix):
                artist = artist[: -len(suffix)].strip()

    if title == "YouTube Music" and not artist:
        return "Idle (YouTube Music)", norm_title

    banned = {"plasma-browser-integration", "firefox", "chrome", "chromium"}
    is_banned = artist.lower() in banned
    clean_artist = "" if is_banned else artist

    prefix = "Listening to:" if status == "Playing" else "Paused:"

    if clean_artist:
        activity = f"{prefix} {norm_title} - {clean_artist}"
    else:
        activity = f"{prefix} {norm_title}"

    if global_provider and global_provider not in activity:
        activity += f" | {global_provider}"

    return activity, norm_title


def _get_best_mpris_activity(lines: list[str]) -> tuple[str, str]:
    """Parse multiple player lines and extract the best metadata."""
    best_activity = "Idle"
    best_title = ""
    best_quality = 0

    global_provider = ""
    for line in lines:
        lower_line = line.lower()
        for key, name in PROVIDERS.items():
            if key in lower_line:
                if (
                    name == "Last.fm"
                    and global_provider
                    and global_provider != "Last.fm"
                ):
                    continue
                global_provider = name

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue

        activity, title = parse_mpris_data(raw, global_provider)

        quality = 0
        if activity.startswith("Listening to:"):
            quality = 20 if " - " in activity else 10
            if global_provider and f"| {global_provider}" in activity:
                quality += 1
        elif activity.startswith("Paused:"):
            quality = -1
        elif activity != "Idle" and not activity.startswith("Idle"):
            quality = 10

        if quality > best_quality:
            best_activity = activity
            best_title = title
            best_quality = quality

    return best_activity, best_title


async def monitor_mpris(updater: MatrixStatusUpdater):
    """Monitor MPRIS events via playerctl by polling all players."""
    while True:
        try:
            # We poll playerctl instead of --follow to avoid holding a persistent
            # D-Bus connection. This allows apps like Firefox to close without
            # warning about an active media control lock.
            # Using --all-players ensures we don't accidentally poll a paused tab
            # when another tab is actively playing.
            process = await asyncio.create_subprocess_exec(
                "playerctl",
                "--all-players",
                "metadata",
                "--format",
                f"{{{{status}}}}{SEP_STR}{{{{title}}}}{SEP_STR}{{{{artist}}}}{SEP_STR}{{{{playerName}}}}",  # noqa: E501
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            stdout, _ = await process.communicate()
            if stdout:
                lines = stdout.decode("utf-8").strip().splitlines()
                activity, title = _get_best_mpris_activity(lines)
                await updater.update(activity, title=title)

        except asyncio.CancelledError:
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(5)


async def main():
    """Start the Matrix updater."""
    # pylint: disable=unused-variable
    lock_fd = acquire_lock()  # noqa: F841
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(HOMESERVER, USERNAME, ACCESS_TOKEN)
    print(f"Matrix User: {USERNAME} on {HOMESERVER}", flush=True)

    async def keep_alive():
        """Keep status online."""

        async def sync_loop():
            while True:
                try:
                    await updater.client.sync(timeout=30, set_presence="online")
                except asyncio.CancelledError:
                    break
                except (asyncio.TimeoutError, OSError):
                    await asyncio.sleep(5)

        async def update_loop():
            while True:
                try:
                    await updater.update(updater.last_activity, force=True)
                    await asyncio.sleep(15)
                except asyncio.CancelledError:
                    break
                except (asyncio.TimeoutError, OSError):
                    await asyncio.sleep(5)

        await asyncio.gather(sync_loop(), update_loop())

    try:
        print("Listening for MPRIS events...", flush=True)
        await asyncio.gather(monitor_mpris(updater), keep_alive())
    except asyncio.CancelledError:
        pass
    finally:
        await updater.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
