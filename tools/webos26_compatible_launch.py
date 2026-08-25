#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Launch the webOS 26 compatibility page through Voice Assistant.

This helper serves the compatibility payload from the computer, pairs with the
TV over SSAP, and points com.webos.app.voiceweb at the local page. It is meant
for the documented webOS 26 fallback after the regular SlopBro flow stalls.
"""

from __future__ import print_function

import argparse
import os
import sys
import time


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import slopbro  # noqa: E402


COMPAT_FILES = [
    "run-webos26-compatible.html",
    "package-webos26.json",
    "services.json",
    "exploit-webos26.js",
    "autoroot-webos26.sh",
]


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the experimental webOS 26 Voice Assistant fallback"
    )
    parser.add_argument("tv_ip", help="TV IPv4 address, for example 192.168.1.50")
    parser.add_argument(
        "--local-ip",
        help="computer LAN IPv4 address; use only if automatic detection is wrong",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="preferred local HTTP port (default: 8080)",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=90,
        help="seconds to keep the payload server available (default: 90)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate files and print the URL without connecting to the TV",
    )
    return parser.parse_args(argv)


def connect_client(tv_ip, local_ip):
    ws = slopbro.WebSocket.connect(
        tv_ip,
        slopbro.PORT_TLS,
        secure=True,
        source_address=(local_ip, 0),
    )
    client = slopbro.SSAPClient(ws)
    stored_key = slopbro.load_client_key(tv_ip)
    new_key = client.register(stored_key, slopbro.DEFAULT_MANIFEST)
    if new_key and new_key != stored_key:
        slopbro.save_client_key(tv_ip, new_key)
        print("[ok] Pairing accepted; the SSAP key was saved locally.")
    return client


def main(argv=None):
    args = parse_args(argv)
    os.chdir(REPO_ROOT)

    wwwroot = slopbro.resolve_wwwroot_path()
    missing = [
        name for name in COMPAT_FILES if not os.path.isfile(os.path.join(wwwroot, name))
    ]
    if missing:
        print("[error] Missing compatibility files: %s" % ", ".join(missing))
        return 2

    try:
        local_ip = slopbro.guess_local_ip(args.tv_ip, args.local_ip)
    except Exception as exc:
        print("[error] Could not determine the computer LAN IP: %s" % exc)
        print("        Retry with --local-ip <COMPUTER_LAN_IP>.")
        return 2

    tracker = slopbro.RequestedFilesTracker(COMPAT_FILES)
    server = None
    client = None
    try:
        server = slopbro.start_http_server(
            wwwroot,
            tracker,
            COMPAT_FILES,
            allow_embedded_assets=False,
            allow_filesystem_fallback=True,
            bind_host="0.0.0.0",
            preferred_port=args.port,
        )
        attempt = int(time.time())
        url = "http://%s:%d/run-webos26-compatible.html?attempt=%d" % (
            local_ip,
            server.server_port,
            attempt,
        )
        print("[ok] Compatibility files are ready at:")
        print("     %s" % url)

        if args.dry_run:
            print("[ok] Dry run complete; the TV was not contacted.")
            return 0

        print("[action] Open Voice Assistant on the TV and keep its panel visible.")
        print("[action] Accept the TV pairing prompt if one appears.")
        client = connect_client(args.tv_ip, local_ip)

        # On the tested webOS 26 firmware, first ensuring that voiceweb is active
        # and then relaunching it with URL was more reliable than close+launch.
        first = slopbro.launch_app(client, "com.webos.app.voiceweb", {})
        print("[ok] Voice Assistant activation response: %s" % first)
        time.sleep(1.0)
        launched = slopbro.launch_app(
            client,
            "com.webos.app.voiceweb",
            {"URL": url},
        )
        print("[ok] Compatibility page launch response: %s" % launched)
        client.close()
        client = None

        print("[wait] Do not turn off the TV or close this terminal.")
        all_requested = tracker.wait_for_all(args.wait)
        if not all_requested:
            print(
                "[error] The TV did not request: %s"
                % ", ".join(tracker.missing_files())
            )
            print("        Reopen Voice Assistant and run this command once more.")
            return 1

        print("[ok] The TV requested every compatibility file.")
        print("[wait] Giving autoroot 25 seconds to install Homebrew Channel...")
        time.sleep(25)
        print("[done] Open Homebrew Channel and verify that Root status is OK.")
        print("       If it is not OK, do not reboot the TV.")
        return 0
    except KeyboardInterrupt:
        print("\n[stopped] Interrupted by the user.")
        return 130
    except Exception as exc:
        print("[error] %s" % exc)
        return 1
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
