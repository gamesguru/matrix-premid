#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Matrix Presence Updater.

A robust script to update Matrix presence and Element status based on
native Linux MPRIS (playerctl) events.
"""

import argparse
import asyncio
import fcntl
import html
import logging
import os
import shutil
import signal
import sys

import argcomplete
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

# Load environment variables
_script_dir = os.path.dirname(os.path.realpath(__file__))
_local_env = os.path.join(_script_dir, ".env")
_config_env = os.path.expanduser("~/.config/matrix-premid/.env")

if os.path.exists(_local_env):
    load_dotenv(dotenv_path=_local_env)
elif os.path.exists(_config_env):
    load_dotenv(dotenv_path=_config_env)
else:
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


# --- CONFIGURATION ---
HOMESERVER = os.environ.get("HOMESERVER", "")
USERNAME = os.environ.get("USERNAME", "")
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
# Timeout (in seconds) before actually clearing the Matrix status when idle
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT", "15"))
# Polling interval (in seconds) for MPRIS events
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))


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
                max_strikes = max(1, IDLE_TIMEOUT // POLL_INTERVAL)
                if self.idle_strikes < max_strikes and not force:
                    # Debounce: wait for IDLE_TIMEOUT seconds of 'Idle'
                    # before actually clearing the Matrix status, preventing
                    # flickering during gaps between songs.
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

            if is_exit:
                await self._send_update(activity, is_exit)
                return

            async def debounced_send():
                if not force:
                    # Wait 2 seconds to absorb rapid metadata shifts
                    # e.g. "Watching: X" -> "Listening to: X - Y"
                    await asyncio.sleep(2.0)
                await self._send_update(activity, is_exit)

            self._update_task = asyncio.create_task(debounced_send())

    async def _send_update(self, activity: str, is_exit: bool = False):
        try:
            # 1. Presence Payload
            path_p = ["presence", self.client.user_id, "status"]
            # pylint: disable=protected-access
            full_path_p = Api._build_path(
                path_p, {"access_token": self.client.access_token}
            )

            if is_exit:
                payload_p = {
                    "presence": "unavailable",  # 'Away' state
                    "currently_active": False,
                    "status_msg": "AFK",
                }
            else:
                payload_p = {
                    "presence": self.current_presence,
                    "currently_active": True,
                }
                if activity and activity != "Idle":
                    payload_p["status_msg"] = activity

            # 2. Element Status Payload
            path_s = [
                "user",
                self.client.user_id,
                "account_data",
                "im.vector.user_status",
            ]
            full_path_s = Api._build_path(
                path_s, {"access_token": self.client.access_token}
            )
            if is_exit:
                payload_s = {"status": "AFK"}
            elif activity == "Idle" or not activity:
                payload_s = {}
            else:
                payload_s = {"status": activity}

            async def send_presence():
                p, c, m = (
                    payload_p["presence"],
                    payload_p["currently_active"],
                    payload_p.get("status_msg", ""),
                )
                print(f"DEBUG: Req 1/2 (presence={p}, active={c}, msg='{m}')")
                try:
                    resp = await asyncio.wait_for(
                        self.client._send(
                            PresenceSetResponse,
                            "PUT",
                            full_path_p,
                            data=Api.to_json(payload_p),
                        ),
                        timeout=5.0 if is_exit else 10.0,
                    )
                    if isinstance(resp, ErrorResponse):  # pragma: no cover
                        msg = getattr(resp, "message", resp)
                        print(f"ERROR: presence failed: {msg}", file=sys.stderr)
                    else:
                        print("DEBUG: Req 1/2 finished (presence)")
                except asyncio.TimeoutError:
                    print("DEBUG: Req 1/2 timeout (presence)", file=sys.stderr)

            async def send_status():
                print(f"DEBUG: Req 2/2 (im.vector.user_status, content={payload_s})")
                try:
                    resp = await asyncio.wait_for(
                        self.client._send(
                            EmptyResponse,
                            "PUT",
                            full_path_s,
                            data=Api.to_json(payload_s),
                        ),
                        timeout=5.0 if is_exit else 10.0,
                    )
                    if isinstance(resp, ErrorResponse):  # pragma: no cover
                        msg = getattr(resp, "message", resp)
                        print(f"ERROR: account_data failed: {msg}", file=sys.stderr)
                    else:
                        print("DEBUG: Req 2/2 finished (account_data)")
                except asyncio.TimeoutError:
                    print("DEBUG: Req 2/2 timeout (account_data)", file=sys.stderr)

            await asyncio.gather(send_presence(), send_status())

        except asyncio.CancelledError:
            pass
        # pylint: disable=broad-exception-caught
        except Exception as e:  # pragma: no cover
            print(f"ERROR: Matrix update exception: {e}", file=sys.stderr)


def _detect_provider_from_url(url: str) -> str:
    """Detect the provider based on the xesam:url metadata."""
    if not url:
        return ""
    url_lower = url.lower()
    if "music.youtube.com" in url_lower:
        return "YouTube Music"
    if "youtube.com/watch" in url_lower or "youtube.com/v/" in url_lower:
        return "YouTube"
    if "netflix.com" in url_lower:
        return "Netflix"
    if "twitch.tv" in url_lower:
        return "Twitch"
    return ""


def _clean_suffixes(title: str, artist: str) -> tuple[str, str]:
    """Remove provider suffixes from title and artist."""
    norm_title = title.strip()
    norm_artist = artist.strip()
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
            if norm_artist.endswith(suffix):
                norm_artist = norm_artist[: -len(suffix)].strip()
    return norm_title, norm_artist


def parse_mpris_data(
    data: str, global_provider: str = "", url: str = ""
) -> tuple[str, str]:
    """Parse playerctl data into (activity_string, normalized_title)."""
    # Browsers often double-escape MPRIS metadata, so we unescape aggressively
    data = html.unescape(html.unescape(data))
    data = data.replace("&quot;", '"').replace("&apos;", "'").replace("&#39;", "'")

    parts = [p.strip() for p in data.split(SEP_STR)]
    if not parts or not parts[0]:
        return "Idle", ""

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

    if parts[0] not in ("Playing", "Paused"):
        return "Idle", ""

    # Use URL to explicitly set/refine the provider if available
    url_provider = _detect_provider_from_url(url)
    if url_provider:
        global_provider = url_provider

    norm_title, norm_artist = _clean_suffixes(title, artist)

    if title == "YouTube Music" and not artist:
        return "Idle (YouTube Music)", norm_title

    banned = {"plasma-browser-integration", "firefox", "chrome", "chromium"}
    is_banned = norm_artist.lower() in banned
    clean_artist = "" if is_banned else norm_artist

    if parts[0] == "Playing":
        prefix = "Watching:" if global_provider in VIDEO_PROVIDERS else "Listening to:"
    else:
        prefix = "Paused:"

    activity = f"{prefix} {norm_title}"
    if clean_artist:
        activity += f" - {clean_artist}"

    if global_provider and global_provider not in activity:
        activity += f" | {global_provider}"

    return activity, norm_title


def _get_line_provider(raw: str) -> str:
    """Detect provider for a single line."""
    lower_line = raw.lower()
    for key in sorted(PROVIDERS.keys(), key=len, reverse=True):
        if key in lower_line:
            return PROVIDERS[key]
    return ""


def _apply_provider_inheritance(parsed_lines: list[dict]):
    """Second pass: Inheritance for players without provider."""
    providers = {p["provider"] for p in parsed_lines if p["provider"]}
    for item in parsed_lines:
        if item["provider"]:
            continue
        raw_parts = item["raw"].split(SEP_STR)
        raw_title = raw_parts[1] if len(raw_parts) > 1 else ""
        for other in parsed_lines:
            if not other["provider"]:
                continue
            # Inherit if titles match OR if there is only one provider in the batch
            if raw_title in other["raw"] or len(providers) == 1:
                item["provider"] = other["provider"]
                break


def _get_best_mpris_activity(lines: list[str]) -> tuple[str, str]:
    """Parse multiple player lines and extract the best metadata."""
    best_activity, best_title, best_quality = "Idle", "", 0

    # First pass: Parse each line and detect its own provider
    parsed_lines = []
    for raw in lines:
        raw = raw.strip()
        if not raw or SEP_STR not in raw:
            continue
        parts = raw.split(SEP_STR)
        url = parts[4] if len(parts) > 4 else ""
        parsed_lines.append(
            {"raw": raw, "provider": _get_line_provider(raw), "url": url}
        )

    _apply_provider_inheritance(parsed_lines)

    # Third pass: Evaluate quality
    for item in parsed_lines:
        activity, title = parse_mpris_data(item["raw"], item["provider"], item["url"])
        quality = 0
        if activity.startswith(("Listening to:", "Watching:")):
            quality = 20 if " - " in activity else 10
            if item["provider"] and f"| {item['provider']}" in activity:
                quality += 1
        elif activity.startswith("Paused:"):
            quality = 5
        elif activity not in ("", "Idle") and not activity.startswith("Idle"):
            quality = 10

        if quality > best_quality:
            best_activity, best_title, best_quality = activity, title, quality

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
                f"{{{{artist}}}}{SEP_STR}{{{{playerName}}}}{SEP_STR}{{{{xesam:url}}}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )

            stdout, _ = await process.communicate()
            if stdout:
                lines = stdout.decode("utf-8").strip().splitlines()
                if "--debug" in sys.argv:
                    print(f"DEBUG: raw playerctl lines: {lines}", flush=True)
                activity, title = _get_best_mpris_activity(lines)
                await updater.update(activity, title=title)

        except asyncio.CancelledError:
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(POLL_INTERVAL)


async def main():
    """Start the Matrix updater."""
    # pylint: disable=too-many-statements
    parser = argparse.ArgumentParser(description="Matrix Presence/PreMiD Updater")
    parser.add_argument(
        "--debug", action="store_true", help="Enable verbose debug logging"
    )
    parser.add_argument(
        "--unset",
        "--clear",
        action="store_true",
        help="Manually clear status to AFK and exit",
    )
    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if args.debug:
        logging.getLogger("nio").setLevel(logging.DEBUG)
    else:
        logging.getLogger("nio").setLevel(logging.CRITICAL)

    if not args.unset and not shutil.which("playerctl"):
        print("ERROR: playerctl command not found. Please install it.", file=sys.stderr)
        sys.exit(1)

    if not all([HOMESERVER, USERNAME, ACCESS_TOKEN]):
        print("ERROR: Missing configuration in .env", file=sys.stderr)
        sys.exit(1)

    updater = MatrixStatusUpdater(
        HOMESERVER, USERNAME, ACCESS_TOKEN, device_id=DEVICE_ID
    )

    if args.unset:
        print("Manual status clear requested (AFK)...", flush=True)
        try:
            await asyncio.wait_for(
                updater.update("", force=True, is_exit=True), timeout=10.0
            )
            await asyncio.sleep(0.5)
        # pylint: disable=broad-exception-caught
        except (Exception, asyncio.CancelledError) as e:
            print(f"ERROR: Manual clear failed: {e}", file=sys.stderr)
        await updater.close()
        return

    # pylint: disable=unused-variable
    lock_fd = acquire_lock()  # noqa: F841
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

        async def sync_loop():  # pragma: no cover
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
            updater.update("", force=True, is_exit=True), timeout=5.0
        )
        # Give nio a moment to actually flush the requests to the network
        await asyncio.sleep(0.5)
    except (  # pylint: disable=broad-exception-caught
        Exception,
        asyncio.CancelledError,
    ):
        pass

    await updater.close()


def cli():
    """Synchronous entry point for the package."""
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    cli()
