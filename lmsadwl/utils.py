"""Utility functions."""

import json
import re
import shutil
import subprocess
import time
from base64 import b64decode


def is_valid_sn(sn):
    return bool(re.match(r"^[A-HJ-NP-Z0-9]{8}$", sn.upper()))


def is_valid_imei(imei):
    return bool(re.match(r"^[0-9]{14,15}$", imei))


def has_adb():
    return shutil.which("adb") is not None


def adb_shell(serial, prop):
    try:
        r = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", prop],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def get_connected_devices():
    if not has_adb():
        return []
    try:
        r = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        devices = []
        for line in r.stdout.strip().splitlines()[1:]:
            if "\tdevice" in line or " device " in line:
                parts = line.split()
                serial = parts[0]
                info = {}
                for p in parts[2:]:
                    if ":" in p:
                        k, v = p.split(":", 1)
                        info[k] = v
                devices.append({
                    "serial": serial,
                    "model": info.get("model", ""),
                    "product": info.get("product", ""),
                })
        return devices
    except Exception:
        return []


def read_device_props(serial):
    from .models import DeviceInfo

    props = {}
    keys = {
        "model": "ro.product.model",
        "brand": "ro.product.brand",
        "fingerprint": "ro.build.fingerprint",
        "serial": "ro.serialno",
        "incremental": "ro.build.version.incremental",
        "sdk": "ro.build.version.sdk",
        "display_id": "ro.build.display.id",
        "carrier": "ro.carrier",
        "market_name": "ro.product.marketname",
        "device": "ro.product.device",
    }
    for field, prop in keys.items():
        val = adb_shell(serial, prop)
        if val:
            props[field] = val
    props["serial"] = serial
    return DeviceInfo.from_dict(props)


def parse_jwt(token):
    """Decode a JWT token payload without verification. Returns dict or None."""
    try:
        # Strip Bearer prefix if present
        t = token
        if t.lower().startswith("bearer "):
            t = t[7:]
        parts = t.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        # Fix base64 padding
        payload += "=" * (4 - len(payload) % 4)
        data = json.loads(b64decode(payload))
        return data
    except Exception:
        return None


def jwt_is_expired(token, margin_seconds=300):
    """Check if a JWT token is expired (with a margin, default 5 min)."""
    claims = parse_jwt(token)
    if not claims:
        return True
    exp = claims.get("exp", 0)
    return time.time() >= (exp - margin_seconds)


def jwt_expiry(token):
    """Return the expiry time of a JWT token as a Unix timestamp, or 0."""
    claims = parse_jwt(token)
    if not claims:
        return 0
    return claims.get("exp", 0)


def jwt_time_left(token):
    """Return seconds until token expires, or 0 if expired/unknown."""
    exp = jwt_expiry(token)
    if not exp:
        return 0
    return max(0, exp - time.time())
