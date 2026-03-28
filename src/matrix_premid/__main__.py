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
import json
import logging
import os
import shutil
import signal
import subprocess
import sys

import aiohttp
import argcomplete
import keyring

try:
    from matrix_premid._version import __version__
except ImportError:  # pragma: no cover
    __version__ = "unknown"

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

# Lock file to prevent multiple instances
LOCK_FILE = os.environ.get("PREMID_LOCK_FILE", "/tmp/matrix-premid.lock")


def acquire_lock():
    """Ensure only one instance runs. Returns the file descriptor."""
    try:
        # We need to keep the file open for the duration of the process
        # pylint: disable=consider-using-with
        lock_fd = open(LOCK_FILE, "a+", encoding="utf-8")
        lock_fd.seek(0)
        fcntl.lockf(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.truncate()
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        return lock_fd
    except OSError:
        print("ERROR: Another instance is already running.", file=sys.stderr)
        sys.exit(1)


class MatrixStatusUpdater:
    """Manages Matrix status updates with state tracking and error handling."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        homeserver,
        username,
        access_token,
        device_id=None,
        idle_timeout=15,
        poll_interval=5,
        verbose=False,
    ):
        # pylint: disable=too-many-arguments,too-many-positional-arguments
        self.homeserver = homeserver.rstrip("/")
        self.username = username
        self.access_token = access_token
        self.device_id = device_id

        self.idle_timeout = idle_timeout
        self.poll_interval = poll_interval
        self.verbose = verbose
        self.last_activity = ""
        self.last_title = ""
        self.last_quality = 0  # 0: Idle, 1: Basic, 2: Full (Artist)
        self.current_presence = "online"  # Default fallback
        self.idle_strikes = 0
        self.lock = asyncio.Lock()
        self._update_task = None
        self._session = None

    async def _get_session(self):
        """Create or return existing aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        """Close the Matrix client session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def update(
        self, activity: str, title: str = "", force: bool = False, is_exit: bool = False
    ):
        """Update Matrix presence with metadata quality filtering."""
        # pylint: disable=too-many-branches, too-many-statements, too-many-locals
        if not activity and not is_exit:
            # If no activity detected, we don't force 'Idle' immediately.
            # We preserve the last activity unless it's explicitly 'Idle'
            # or we are exiting.
            return

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
            if not force and not is_exit and title and title == self.last_title:
                if quality < self.last_quality:
                    return

            is_new = activity != self.last_activity
            if not force and not is_new and not is_exit:
                # Reset strikes if we are consistently playing the same non-idle song
                if activity != "Idle":
                    self.idle_strikes = 0
                return

            is_idle = activity == "Idle" or activity.startswith("Idle")
            if is_idle and not is_exit:
                self.idle_strikes += 1
                max_strikes = max(1, self.idle_timeout // self.poll_interval)
                if self.idle_strikes < max_strikes and not force:
                    # Debounce idle state
                    return
            else:
                self.idle_strikes = 0

            if is_new or is_exit:
                activity_str = "Offline" if is_exit else activity
                print(
                    f"[{self.username}] Matrix Status "
                    f"[{self.current_presence}] -> {activity_str}",
                    flush=True,
                )

            if not is_exit:
                self.last_activity = activity
                self.last_title = title
                self.last_quality = quality

            if self._update_task and not self._update_task.done():
                self._update_task.cancel()

            if is_exit:
                await self.send_update(activity, is_exit)
                return

            async def debounced_send():
                if not force:
                    # Wait 2 seconds to absorb rapid metadata shifts
                    await asyncio.sleep(2.0)
                await self.send_update(activity, is_exit)

            self._update_task = asyncio.create_task(debounced_send())

    async def send_update(self, activity: str, is_exit: bool = False):
        """Send presence and status update to Matrix."""
        try:
            session = await self._get_session()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # 1. Presence Payload
            url_p = (
                f"{self.homeserver}/_matrix/client/v3/presence/"
                f"{self.username}/status"
            )

            if is_exit:
                payload_p = {
                    "presence": "offline",
                    "currently_active": False,
                    "status_msg": "",
                }
            else:
                payload_p = {
                    "presence": self.current_presence,
                    "currently_active": True,
                }
                if activity and activity != "Idle":
                    payload_p["status_msg"] = activity

            # 2. Element Status Payload
            url_s = (
                f"{self.homeserver}/_matrix/client/v3/user/{self.username}/"
                "account_data/im.vector.user_status"
            )

            if is_exit or activity == "Idle" or not activity:
                payload_s = {}
            else:
                payload_s = {"status": activity}

            async def send_presence():
                if self.verbose:
                    print(f"DEBUG [{self.username}]: Req 1/2 (presence)")
                try:
                    async with session.put(
                        url_p,
                        json=payload_p,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5.0 if is_exit else 10.0),
                    ) as resp:
                        if resp.status >= 400:
                            data = await resp.text()
                            print(
                                f"ERROR [{self.username}]: presence failed "
                                f"({resp.status}): {data}",
                                file=sys.stderr,
                            )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    if self.verbose:
                        print(
                            f"DEBUG [{self.username}]: presence error: {e}",
                            file=sys.stderr,
                        )

            async def send_status():
                if self.verbose:
                    print(f"DEBUG [{self.username}]: Req 2/2 (account_data)")
                try:
                    async with session.put(
                        url_s,
                        json=payload_s,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5.0 if is_exit else 10.0),
                    ) as resp:
                        if resp.status >= 400:
                            data = await resp.text()
                            print(
                                f"ERROR [{self.username}]: account_data failed "
                                f"({resp.status}): {data}",
                                file=sys.stderr,
                            )
                except Exception as e:  # pylint: disable=broad-exception-caught
                    if self.verbose:
                        print(
                            f"DEBUG [{self.username}]: account_data error: {e}",
                            file=sys.stderr,
                        )

            await asyncio.gather(send_presence(), send_status())

        except asyncio.CancelledError:
            pass
        except Exception as e:  # pylint: disable=broad-exception-caught
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
        return "", ""

    def _deep_clean(text: str) -> str:
        import ast  # pylint: disable=import-outside-toplevel

        text = html.unescape(html.unescape(text))
        cleaned = (
            text.replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&#39;", "'")
            .replace("&amp;", "&")
            .strip()
        )

        if cleaned.startswith("[") and cleaned.endswith("]"):
            try:
                parsed = ast.literal_eval(cleaned)
                if isinstance(parsed, list):  # pragma: no branch
                    return ", ".join(str(x) for x in parsed)
            except (ValueError, SyntaxError):  # pragma: no cover
                pass

        return cleaned

    title = _deep_clean(parts[1]) if len(parts) > 1 else "Unknown Title"
    artist = _deep_clean(parts[2]) if len(parts) > 2 else ""

    if parts[0] not in ("Playing", "Paused"):
        return "", ""

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
    best_activity, best_title, best_quality = "", "", 0

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
        if not activity:
            continue

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


async def monitor_mpris(updaters: list[MatrixStatusUpdater], poll_interval: int):
    """Monitor MPRIS events via playerctl by polling all players."""
    while True:
        try:
            # We poll playerctl instead of --follow to avoid holding a persistent
            # D-Bus connection.
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
            lines = stdout.decode("utf-8").strip().splitlines() if stdout else []
            if updaters and updaters[0].verbose:  # pragma: no cover
                print(f"DEBUG: raw playerctl lines: {lines}", flush=True)
            activity, title = _get_best_mpris_activity(lines)
            for updater in updaters:
                await updater.update(activity, title=title)

        except asyncio.CancelledError:
            break
        except (OSError, ValueError) as e:
            print(f"MPRIS Monitor Error: {e}", file=sys.stderr)

        await asyncio.sleep(poll_interval)


def install_service():
    """Install the systemd user service."""
    executable = shutil.which("matrix-premid")
    if not executable:
        # Fallback if not in PATH
        executable = sys.executable + " -m matrix_premid"

    service_content = f"""[Unit]
Description=Matrix Presence Updater
After=network.target

[Service]
Type=simple
Environment=PYTHONUNBUFFERED=1
ExecStart={executable}
Restart=on-failure
RestartSec=120

[Install]
WantedBy=default.target
"""
    config_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(config_dir, exist_ok=True)
    service_file = os.path.join(config_dir, "matrix-premid.service")

    with open(service_file, "w", encoding="utf-8") as f:
        f.write(service_content)

    print(f"Created systemd user service at {service_file}")

    app_config_dir = os.path.expanduser("~/.config/matrix-premid")
    os.makedirs(app_config_dir, exist_ok=True)
    config_file = os.path.join(app_config_dir, "config.json")
    if not os.path.exists(config_file):
        sample_config = {
            "accounts": [
                {
                    "homeserver": "https://matrix.org",
                    "username": "@user:matrix.org",
                    "device_id": "",
                }
            ],
            "idle_timeout": 15,
            "poll_interval": 5,
        }
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(sample_config, f, indent=4)
        print(f"Created empty config at {config_file} (Please edit!)")
        print(
            "Note: Store your access token using keyring: "
            "python -m keyring set matrix-premid @user:matrix.org"
        )
    else:
        print(f"Config already exists at: {config_file}")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "matrix-premid.service"],
            check=True,
        )
        print(
            "Service enabled successfully. Start it with: "
            "systemctl --user start matrix-premid.service"
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Failed to enable service: {e}", file=sys.stderr)


def parse_args(args=None):
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Matrix Presence/PreMiD Updater")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["install-service", "daemon", "shutdown", "set"],
        help="Optional command (e.g., install-service, daemon, shutdown, set)",
    )
    parser.add_argument(
        "status_args",
        nargs="*",
        help="Status message for the 'set' command",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable verbose debug logging"
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Show program's version number and exit",
    )
    parser.add_argument(
        "--unset",
        "--clear",
        action="store_true",
        help="Manually clear status (Offline) and exit",
    )
    argcomplete.autocomplete(parser)
    return parser.parse_args(args)


async def main(args=None):
    """Start the Matrix updater."""
    # pylint: disable=too-many-statements,too-many-locals,too-many-branches
    if args is None:
        args = parse_args()

    if args.command == "install-service":  # pragma: no cover
        install_service()
        return

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.CRITICAL)

    if not args.unset and not shutil.which("playerctl"):
        print("ERROR: playerctl command not found. Please install it.", file=sys.stderr)
        sys.exit(1)

    accounts = []
    idle_timeout = 15
    poll_interval = 5

    # Load configuration
    config_file = os.path.expanduser("~/.config/matrix-premid/config.json")
    if not os.path.exists(config_file):
        print(
            "ERROR: Missing configuration. Please run 'matrix-premid install-service' "
            "to create a config.json template.",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)
        accounts = config.get("accounts", [])
        idle_timeout = config.get("idle_timeout", 15)
        poll_interval = config.get("poll_interval", 5)

    if not accounts:
        print("ERROR: No accounts defined in config.json.", file=sys.stderr)
        sys.exit(1)

    # Resolve tokens from keyring if missing
    for account in accounts:
        if not account.get("access_token"):
            token = keyring.get_password("matrix-premid", account["username"])
            if token:
                account["access_token"] = token
            else:
                print(
                    f"ERROR: Missing access token for {account['username']}. "
                    "Set it using: python -m keyring set matrix-premid "
                    f"{account['username']}",
                    file=sys.stderr,
                )
                sys.exit(1)

    updaters = []
    is_debug = args.debug
    for account in accounts:
        updaters.append(
            MatrixStatusUpdater(
                account["homeserver"],
                account["username"],
                account["access_token"],
                device_id=account.get("device_id", ""),
                idle_timeout=idle_timeout,
                poll_interval=poll_interval,
                verbose=is_debug,
            )
        )

    if args.command == "set":
        status_msg = " ".join(args.status_args)
        if not status_msg:
            print("ERROR: 'set' command requires a status message.", file=sys.stderr)
            for u in updaters:
                await u.close()
            sys.exit(1)

        print(
            f"Setting status to: '{status_msg}' for {len(updaters)} accounts...",
            flush=True,
        )
        try:
            # We call send_update directly to ensure it finishes before we exit
            await asyncio.wait_for(
                asyncio.gather(*(u.send_update(status_msg) for u in updaters)),
                timeout=10.0,
            )
            print("Successfully updated status.")
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"ERROR: manual set failed: {e}", file=sys.stderr)

        for u in updaters:
            await u.close()
        return

    if args.unset:
        print(
            f"Manual status clear requested (Offline) for {len(updaters)} accounts...",
            flush=True,
        )
        try:
            # Update all accounts to idle concurrently
            await asyncio.wait_for(
                asyncio.gather(
                    *(u.update("", force=True, is_exit=True) for u in updaters)
                ),
                timeout=10.0,
            )
            await asyncio.sleep(0.5)
            print(f"Successfully cleared status for {len(updaters)} accounts.")
        except asyncio.CancelledError:
            pass
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"ERROR: Manual clear failed: {e}", file=sys.stderr)

        for u in updaters:
            await u.close()
        return

    # pylint: disable=unused-variable
    lock_fd = acquire_lock()  # noqa: F841

    users_str = ", ".join([a["username"] for a in accounts])
    print(f"Matrix Users: {users_str}", flush=True)

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

    async def keep_alive(updater: MatrixStatusUpdater):
        """Keep status online via periodic presence updates."""
        backoff = 5
        while not shutdown_event.is_set():
            try:
                # Instead of syncing, we just push a presence update periodically
                # to stay 'online' in the eyes of the server.
                await updater.send_update(updater.last_activity)

                # Wait for a while before the next refresh
                # Typically Matrix presence expires in 5-15 minutes if not refreshed
                # but we'll be more aggressive to ensure status visibility.
                for _ in range(60):  # 60 seconds
                    if shutdown_event.is_set():
                        break
                    await asyncio.sleep(1)
                backoff = 5
            except asyncio.CancelledError:
                break
            except Exception as e:  # pylint: disable=broad-exception-caught
                print(
                    f"ERROR: keep-alive exception ({updater.username}): {e}",
                    file=sys.stderr,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

    print("Listening for MPRIS events...", flush=True)

    # Run tasks in background
    tasks = [asyncio.create_task(monitor_mpris(updaters, poll_interval))]
    for u in updaters:
        tasks.append(asyncio.create_task(keep_alive(u)))

    # Wait for a shutdown signal
    await shutdown_event.wait()

    # Cancel background tasks
    for t in tasks:
        t.cancel()

    # Wait for tasks to acknowledge cancellation
    await asyncio.gather(*tasks, return_exceptions=True)

    print(
        f"Clearing Matrix status before exit for {len(updaters)} accounts...",
        flush=True,
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*(u.update("", force=True, is_exit=True) for u in updaters)),
            timeout=5.0,
        )
        # Give nio a moment to actually flush the requests to the network
        await asyncio.sleep(0.5)
    except (  # pylint: disable=broad-exception-caught
        Exception,
        asyncio.CancelledError,
    ):
        pass

    for u in updaters:
        await u.close()

    # Clean up lock file so next start works cleanly
    try:
        os.unlink(LOCK_FILE)
    except OSError:
        pass

    print("Done.")


def daemonize():  # pragma: no cover
    """Fork the process to run in the background."""
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        print(f"Fork #1 failed: {e}", file=sys.stderr)
        sys.exit(1)

    os.chdir("/")
    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError as e:
        print(f"Fork #2 failed: {e}", file=sys.stderr)
        sys.exit(1)

    sys.stdout.flush()
    sys.stderr.flush()
    try:
        with open(os.devnull, "r", encoding="utf-8") as f:
            os.dup2(f.fileno(), sys.stdin.fileno())
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def shutdown_daemon():  # pragma: no cover
    """Send SIGTERM to the running background daemon."""
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"Sent shutdown signal to matrix-premid daemon (PID {pid}).")
    except FileNotFoundError:
        print("No matrix-premid daemon is currently running (lock file not found).")
    except ProcessLookupError:
        print("Daemon is not running (PID not found).")
    except ValueError:
        print("Invalid PID in lock file.")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Failed to shutdown daemon: {e}")


def cli():
    """Synchronous entry point for the package."""
    args = parse_args()

    if args.command == "shutdown":  # pragma: no cover
        shutdown_daemon()
        return

    if args.command == "daemon":  # pragma: no cover
        print("Starting matrix-premid in the background...")
        daemonize()

    try:
        asyncio.run(main(args))
    except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    cli()
