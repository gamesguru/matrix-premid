"""Longer running integration test best performed locally or in docker."""

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

# pylint: disable=missing-docstring, too-many-locals
# pylint: disable=too-many-statements, consider-using-with


def test_integration():
    print("🚀 Starting Matrix PreMiD Integration Test...")

    # 1. Register User on local Conduit
    print("📝 Registering dummy user via matrix client API...")
    req = urllib.request.Request(
        "http://localhost:8008/_matrix/client/v3/register",
        data=json.dumps(
            {
                "username": "ci_user",
                "password": "ci_password",
                "auth": {"type": "m.login.dummy"},
            }
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            res = json.loads(resp.read().decode())
            token = res["access_token"]
            device_id = res["device_id"]
            user_id = res["user_id"]
            print(f"✅ Registered user: {user_id}")
    except urllib.error.HTTPError as e:
        print("Registration failed:", e.read().decode())
        sys.exit(1)

    # 2. Setup mock playerctl
    print("🛠️  Setting up mocked playerctl...")
    mock_dir = tempfile.mkdtemp()
    mock_script = os.path.join(mock_dir, "playerctl")
    with open(mock_script, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        # Echo our predetermined test string to spoof what Linux MPRIS outputs
        f.write("echo 'Playing|GitHub Actions Song|Integration Tests|firefox'\n")
    os.chmod(mock_script, 0o755)

    # 3. Write .env inside repo root
    # Note: tests are run from the project root in CI
    print("🌐 Writing local configuration .env file...")
    with open(".env", "w", encoding="utf-8") as f:
        f.write("HOMESERVER=http://localhost:8008\n")
        f.write(f"USERNAME={user_id}\n")
        f.write(f"ACCESS_TOKEN={token}\n")
        f.write(f"DEVICE_ID={device_id}\n")

    # 4. Start matrix_premid.py
    print("🏃 Starting matrix_premid.py background daemon...")
    env = os.environ.copy()
    # Prepend mock_dir so subprocess picks up our fake playerctl instead of real one
    env["PATH"] = f"{mock_dir}:{env['PATH']}"
    proc = subprocess.Popen([sys.executable, "matrix_premid.py"], env=env)

    # 5. Wait for loop to pick up playerctl, parse, and send to homeserver
    print("⏳ Waiting 10 seconds for service to update presence...")
    time.sleep(10)

    # 6. Verify Matrix Status updates
    try:
        print("🔍 Verifying Presence API state...")
        expected = "Listening to: GitHub Actions Song - Integration Tests"

        encoded_user = urllib.parse.quote(user_id)
        url = f"http://localhost:8008/_matrix/client/v3/presence/{encoded_user}/status"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req) as resp:
            pres = json.loads(resp.read().decode())
            assert pres["presence"] == "online", f"Presence not online: {pres}"
            assert pres["status_msg"] == expected, f"Status MSG mismatch: {pres}"
            print(f"✅ Presence successfully verified: {pres['status_msg']}")

        print("🔍 Verifying Account Data API state (im.vector.user_status)...")
        encoded_user = urllib.parse.quote(user_id)
        acc_url = (
            f"http://localhost:8008/_matrix/client/v3/user/"
            f"{encoded_user}/account_data/im.vector.user_status"
        )
        req = urllib.request.Request(
            acc_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req) as resp:
            acc = json.loads(resp.read().decode())
            assert acc["status"] == expected, f"Account Data mismatch: {acc}"
            print(f"✅ Account Data successfully verified: {acc['status']}")

        print("🎉 ALL INTEGRATION TESTS PASSED 🎉")

    except AssertionError as e:
        print(f"❌ TEST FAILED: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        proc.terminate()
        proc.wait()


if __name__ == "__main__":
    test_integration()
