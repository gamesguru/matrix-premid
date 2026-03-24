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
import shutil
import signal
import sys

from dotenv import load_dotenv
from nio import Api, AsyncClient
from nio.responses import EmptyResponse, ErrorResponse, PresenceSetResponse

PROVIDERS = {
    "youtube music": "YouTube Music",
    "yt music": "YouTube Music",
    "music.youtube.com": "YouTube Music",
    "youtube": "YouTube",
    "spotify": "Spotify",
    "netflix": "Netflix",
    "plex": "Plex",
    "soundcloud": "SoundCloud",
    "last.fm": "Last.fm",
    "twitch": "Twitch",
    "apple music": "Apple Music",
}

VIDEO_PROVIDERS = {"Netflix", "Plex", "Twitch", "YouTube"}

SEP_STR = "_||_"

# Load environment variables from .env if present
load_dotenv()

# Lock file to prevent multiple instances
LOCK_FILE = os.environ.get("PREMID_LOCK_FILE", "/tmp/matrix-premid.lock")


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


DEBUG_MODE = "--debug" in sys.argv

if DEBUG_MODE:
    logging.getLogger("nio").setLevel(logging.DEBUG)
else:
    # Suppress noisy matrix-nio validation errors
    logging.getLogger("nio").setLevel(logging.CRITICAL)

# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")


class MatrixStatusUpdater:
    """Manages Matrix status updates with state tracking and error handling."""

    # pylint: disable=too-many-instance-attributes

    def __init__(self, homeserver, username, access_token, device_id=None):
        self.client = AsyncClient(homeserver, username)
        self.client.access_token = access_token
        self.client.user_id = username
        if device_id:
            self.client.device_id = device_id

        self.last_activity = ""
        self.last_title = ""
        self.last_quality = 0  # 0: Idle, 1: Basic, 2: Full (Artist)
        self.current_presence = "online"  # Default fallback
        self.idle_strikes = 0
        self.lock = asyncio.Lock()
        self._update_task = None

    async def close(self):
        """Close the Matrix client session."""
        await self.client.close()

    async def update(
        self, activity: str, title: str = "", force: bool = False, is_exit: bool = False
    ):
        """Update Matrix presence with metadata quality filtering."""
        # pylint: disable=too-many-branches, too-many-statements, too-many-locals
        if not activity and not is_exit:
            activity = "Idle"

        # Determine metadata quality
        quality = 0
        if activity.startswith("Listening to:") or activity.startswith("Watching:"):
            quality = 20 if " - " in activity else 10
            if "YT Music" in activity:
                quality += 1
        elif activity.startswith("Paused:"):
            quality = 6 if " - " in activity else 4
            if "YT Music" in activity:
                quality += 1
        elif activity != "Idle" and not activity.startswith("Idle") and activity != "":
            quality = 10

        async with self.lock:
            # If same song but lower quality metadata, ignore it
            # This prevents Firefox (no artist) from overriding Plasma (artist)
            if not force and not is_exit and title and title == self.last_title:
                if quality < self.last_quality:
                    return

            is_new = activity != self.last_activity
            if not force and not is_new and not is_exit:
                # Reset strikes if we are consistently playing the same non-idle song
                if activity != "Idle":
                    self.idle_strikes = 0
                return

            if activity == "Idle" and not is_exit:
                self.idle_strikes += 1
                if self.idle_strikes < 3 and not force:
                    # Debounce: wait for 3 consecutive polls (15s) of 'Idle'
                    # before actually clearing the Matrix status, preventing
                    # flickering during 1-2 second gaps between songs.
                    return
            else:
                self.idle_strikes = 0

            if is_new and not is_exit:
                print(
                    f"Matrix Status [{self.current_presence}] -> {activity}", flush=True
                )

            if not is_exit:
                self.last_activity = activity
                self.last_title = title
                self.last_quality = quality

            if self._update_task and not self._update_task.done():
                self._update_task.cancel()

            async def debounced_send():
                if not force and not is_exit:
                    # Wait 2 seconds to absorb rapid metadata shifts
                    # e.g. "Watching: X" -> "Listening to: X - Y"
                    await asyncio.sleep(2.0)
                await self._send_update(activity, is_exit)

            self._update_task = asyncio.create_task(debounced_send())

    async def _send_update(self, activity: str, is_exit: bool = False):
        try:
            # 1. Standard Presence
            if is_exit:
                pres_state = "offline"
                pres_msg = ""
            else:
                pres_state = self.current_presence
                pres_msg = activity if activity != "Idle" else ""

            print(
                f"DEBUG: Request 1/3 - set_presence(presence='{pres_state}', "
                f"status_msg='{pres_msg}')"
            )
            resp1 = await self.client.set_presence(
                presence=pres_state, status_msg=pres_msg
            )
            if isinstance(resp1, ErrorResponse):  # pragma: no cover
                print(
                    "ERROR: set_presence failed: "
                    f"{getattr(resp1, 'message', resp1)}",
                    file=sys.stderr,
                )

            path = ["presence", self.client.user_id, "status"]
            # pylint: disable=protected-access
            full_path = Api._build_path(
                path, {"access_token": self.client.access_token}
            )

            if is_exit:
                payload = {"presence": "offline"}
            else:
                payload = {
                    "presence": self.current_presence,
                    "currently_active": True,
                }
                if activity and activity != "Idle":
                    payload["status_msg"] = activity

            print("DEBUG: Request 2/3 - custom PUT presence (currently_active=True)")
            resp2 = await self.client._send(
                PresenceSetResponse,
                "PUT",
                full_path,
                data=Api.to_json(payload),
            )
            if isinstance(resp2, ErrorResponse):  # pragma: no cover
                print(
                    "ERROR: custom presence failed: "
                    f"{getattr(resp2, 'message', resp2)}",
                    file=sys.stderr,
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
            content = (
                {"status": activity}
                if activity and activity != "Idle" and not is_exit
                else {}
            )

            print("DEBUG: Request 3/3 - PUT account_data (im.vector.user_status)")
            resp3 = await self.client._send(
                EmptyResponse, "PUT", full_path, data=Api.to_json(content)
            )
            if isinstance(resp3, ErrorResponse):  # pragma: no cover
                print(
                    "ERROR: account_data failed: "
                    f"{getattr(resp3, 'message', resp3)}",
                    file=sys.stderr,
                )

        except asyncio.CancelledError:
            pass
        # pylint: disable=broad-exception-caught
        except Exception as e:  # pragma: no cover
            print(f"ERROR: Matrix update exception: {e}", file=sys.stderr)


def parse_mpris_data(data: str, global_provider: str = "") -> tuple[str, str]:
    """Parse playerctl data into (activity_string, normalized_title)."""
    # Browsers often double-escape MPRIS metadata, so we unescape aggressively
    data = html.unescape(html.unescape(data))
    data = data.replace("&quot;", '"').replace("&apos;", "'").replace("&#39;", "'")

    parts = [p.strip() for p in data.split(SEP_STR)]
    if not parts or not parts[0]:
        return "Idle", ""

    status = parts[0]

    def _deep_clean(text: str) -> str:
        text = html.unescape(html.unescape(text))
        return (
            text.replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&#39;", "'")
            .replace("&amp;", "&")
            .strip()
        )

    title = _deep_clean(parts[1]) if len(parts) > 1 else "Unknown Title"
    artist = _deep_clean(parts[2]) if len(parts) > 2 else ""

    if status not in ("Playing", "Paused"):
        return "Idle", ""

    norm_title = title.strip()

    # Check longest providers first to prevent substring bugs
    # (e.g. YouTube vs YouTube Music)
    for provider in sorted(set(PROVIDERS.values()), key=len, reverse=True):
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

    if status == "Playing":
        prefix = "Watching:" if global_provider in VIDEO_PROVIDERS else "Listening to:"
    else:
        prefix = "Paused:"

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
        # Check longest keys first to prevent substring matches
        # (e.g. YouTube vs YouTube Music)
        for key in sorted(PROVIDERS.keys(), key=len, reverse=True):
            name = PROVIDERS[key]
            if key in lower_line:
                if (
                    name == "Last.fm"
                    and global_provider
                    and global_provider != "Last.fm"
                ):
                    continue
                global_provider = name
                break

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue

        activity, title = parse_mpris_data(raw, global_provider)

        quality = 0
        if activity.startswith("Listening to:") or activity.startswith("Watching:"):
            quality = 20 if " - " in activity else 10
            if global_provider and f"| {global_provider}" in activity:
                quality += 1
        elif activity.startswith("Paused:"):
            # Ensure paused tracks win over "Idle" (0) but lose to "Playing" (10+)
            quality = 5
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
                f"{{{{status}}}}{SEP_STR}{{{{title}}}}{SEP_STR}"
                f"{{{{artist}}}}{SEP_STR}{{{{playerName}}}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            stdout, _ = await process.communicate()
            if stdout:
                lines = stdout.decode("utf-8").strip().splitlines()
                if DEBUG_MODE:
                    print(f"DEBUG: raw playerctl lines: {lines}", flush=True)
                activity, title = _get_best_mpris_activity(lines)
                await updater.update(activity, title=title)

        except asyncio.CancelledError:
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(5)


async def main():
    """Start the Matrix updater."""
    # pylint: disable=too-many-statements

    if not shutil.which("playerctl"):
        print("ERROR: playerctl command not found. Please install it.", file=sys.stderr)
        sys.exit(1)

    # pylint: disable=unused-variable
    lock_fd = acquire_lock()  # noqa: F841
    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(
        HOMESERVER, USERNAME, ACCESS_TOKEN, device_id=DEVICE_ID
    )
    print(f"Matrix User: {USERNAME} on {HOMESERVER}", flush=True)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_handler():
        print("\nInitiating graceful shutdown...", flush=True)
        shutdown_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)
    except NotImplementedError:  # pragma: no cover
        pass

    async def keep_alive():
        """Keep status online."""

        async def sync_loop():
            while True:
                try:
                    # Sync with set_presence="online" to brutally override Nheko
                    # hiding the 'Busy' (dnd) state.
                    resp = await updater.client.sync(timeout=30, set_presence="online")
                    if isinstance(resp, ErrorResponse):  # pragma: no cover
                        msg = getattr(resp, "message", resp)
                        print(f"ERROR: sync failed: {msg}", file=sys.stderr)
                        if getattr(resp, "status_code", 0) in (401, 403):
                            print("ERROR: Unauthorized. Exiting.", file=sys.stderr)
                            shutdown_event.set()
                            break
                    # We no longer read event.presence to inherit 'dnd'/'busy'
                    # because the user explicitly wants to be forced Online.
                    if updater.current_presence != "online":
                        updater.current_presence = "online"
                except asyncio.CancelledError:
                    break
                except (
                    # pylint: disable=broad-exception-caught # pragma: no cover
                    Exception
                ) as e:
                    print(
                        f"ERROR: sync loop exception: {type(e).__name__} - {e}",
                        file=sys.stderr,
                    )
                    await asyncio.sleep(5)

        await sync_loop()

    print("Listening for MPRIS events...", flush=True)

    # Run tasks in background
    tasks = [
        asyncio.create_task(monitor_mpris(updater)),
        asyncio.create_task(keep_alive()),
    ]

    # Wait for a shutdown signal
    await shutdown_event.wait()

    # Cancel background tasks
    for t in tasks:
        t.cancel()

    print("Clearing Matrix status before exit...", flush=True)
    try:
        await asyncio.wait_for(
            updater.update("", force=True, is_exit=True), timeout=3.0
        )
    except (  # pylint: disable=broad-exception-caught
        Exception,
        asyncio.CancelledError,
    ):
        pass

    await updater.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass
