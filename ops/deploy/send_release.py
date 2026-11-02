#!/usr/bin/env python3
"""Send a bounded release request over verified SSH. Never retry uncertain mutations."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from build_bundle import canonical


def ssh_command(host, port, user, identity_file, known_hosts):
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", host)
            or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", user)
            or not 1 <= int(port) <= 65535):
        raise ValueError("invalid_ssh_destination")
    return ["ssh", "-F", "/dev/null", "-T", "-p", str(int(port)),
            "-i", str(identity_file), "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=1",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-o", "ClearAllForwardings=yes", "-o", "ForwardAgent=no",
            "-o", "LogLevel=ERROR", f"{user}@{host}", "personal-watch-release-v1"]


def safe_result(value):
    """Do not relay arbitrary remote output, paths, diagnostics or tracebacks to CI."""
    if not isinstance(value, dict):
        raise ValueError("invalid_receiver_result")
    result = {}
    for key in ("status", "code", "revision", "release_id", "current", "active", "previous", "recovery_required", "retention_pending"):
        item = value.get(key)
        if item is None or isinstance(item, bool):
            if key in value:
                result[key] = item
        elif isinstance(item, str):
            pattern = r"[0-9a-f]{40}" if key == "revision" else r"[0-9a-f]{64}" if key in ("release_id", "current", "active", "previous") else r"[a-z_]{1,80}"
            if re.fullmatch(pattern, item):
                result[key] = item
    if not result:
        raise ValueError("invalid_receiver_result")
    return result


def send_request(request, host, port, user, private_key, known_hosts):
    payload = canonical(request)
    if len(payload) > 2_000_000 or not private_key.strip() or not known_hosts.strip():
        raise ValueError("invalid_transport_input")
    with tempfile.TemporaryDirectory(prefix="watch-release-ssh-") as directory:
        key = Path(directory) / "identity"
        hosts = Path(directory) / "known_hosts"
        for path, content in ((key, private_key), (hosts, known_hosts)):
            path.touch(mode=0o600)
            path.write_text(content.rstrip() + "\n")
        command = ssh_command(host, port, user, key, hosts)
        # Capture bounded output in a private temporary file, never in Actions logs.
        with tempfile.TemporaryFile() as output:
            process = subprocess.run(command, input=payload, stdout=output,
                                     stderr=subprocess.DEVNULL, timeout=480, check=False,
                                     env={"PATH": "/usr/bin:/bin", "LANG": "C"})
            output.seek(0)
            body = output.read(8193)
        if len(body) > 8192:
            raise ValueError("invalid_receiver_result")
        if process.returncode and not body:
            raise ValueError("ssh_failed_state_unknown_check_status")
        result = safe_result(json.loads(body))
        return process.returncode, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("status", "deploy", "rollback", "recover"), required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--expected-current", default="")
    parser.add_argument("--release-id", default="")
    args = parser.parse_args()
    request = {"action": args.action}
    try:
        if args.action in ("deploy", "rollback"):
            if not re.fullmatch(r"[0-9a-f]{64}", args.expected_current):
                raise ValueError("expected_current_required")
            request["expected_current"] = args.expected_current
        if args.action == "deploy":
            if args.bundle is None or args.bundle.stat().st_size > 2_000_000:
                raise ValueError("bundle_required")
            request["bundle"] = json.loads(args.bundle.read_bytes())
        elif args.action == "rollback":
            if not re.fullmatch(r"[0-9a-f]{64}", args.release_id):
                raise ValueError("release_id_required")
            request["release_id"] = args.release_id
        code, result = send_request(request, os.environ["WATCH_SSH_HOST"],
                                    os.environ.get("WATCH_SSH_PORT") or "22",
                                    os.environ["WATCH_SSH_USER"],
                                    os.environ["WATCH_SSH_PRIVATE_KEY"],
                                    os.environ["WATCH_SSH_KNOWN_HOSTS"])
    except (ValueError, KeyError, OSError, subprocess.SubprocessError):
        parser.exit(1, "release_transport_failed_state_unknown_check_status\n")
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
