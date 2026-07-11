"""lmsadwl CLI — command-line interface for the library."""

import argparse
import hashlib
import os
import re
import sys
import time

from .api import AuthError, LMSAClient
from .auth import REFRESH_MARGIN
from .models import DeviceInfo
from .utils import is_valid_imei, is_valid_sn, jwt_time_left

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GRN = "\033[32m"
YLW = "\033[33m"
MAG = "\033[35m"
CYN = "\033[36m"
WHT = "\033[37m"
BG_GRN = "\033[42m"


def banner():
    print(f"""
{CYN}{BOLD}┌──────────────────────────────────────────────────────┐
│                                                      │
│   ██╗     ███╗   ███╗ █████╗ ███████╗ ██████╗        │
│   ██║     ████╗ ████║██╔══██╗██╔════╝██╔════╝        │
│   ██║     ██╔████╔██║███████║███████╗██║             │
│   ██║     ██║╚██╔╝██║██╔══██║╚════██║██║             │
│   ███████╗██║ ╚═╝ ██║██║  ██║███████║╚██████╗        │
│   ╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝        │
│                                                      │
│   {WHT}lmsadwl v1.0.0{CYN}                                   │
│   {DIM}Official Lenovo servers • No third parties{CYN}           │
│                                                      │
└──────────────────────────────────────────────────────┘{RST}
""")


def _dim(t):
    return f"{DIM}{t}{RST}"


def _ok(t):
    return f"{GRN}{BOLD}{t}{RST}"


def _err(t):
    return f"{RED}{BOLD}{t}{RST}"


def _warn(t):
    return f"{YLW}{t}{RST}"


def _cyn(t):
    return f"{CYN}{t}{RST}"


def _bold(t):
    return f"{BOLD}{t}{RST}"


def _bar(downloaded, total, elapsed, width=40):
    if total == 0:
        return ""
    pct = downloaded / total
    filled = int(width * pct)
    bar = f"{GRN}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"
    mb = downloaded / 1024 / 1024
    total_mb = total / 1024 / 1024
    speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
    if pct > 0 and elapsed > 1:
        remaining = (total - downloaded) / (downloaded / elapsed)
        eta = _fmt_time(remaining)
        eta_str = f" {_dim('ETA ' + eta)}"
    else:
        eta_str = ""
    return f"  {bar} {WHT}{pct*100:5.1f}%{RST}  {_cyn(f'{mb:.1f}/{total_mb:.1f} MB')}  {YLW}{speed:.1f} MB/s{RST}{eta_str}"


def _fmt_time(s):
    if s < 60:
        return f"{int(s)}s"
    elif s < 3600:
        m, s = divmod(int(s), 60)
        return f"{m}m {s:02d}s"
    else:
        h, rem = divmod(int(s), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m:02d}m"


def _prompt(msg, default=""):
    try:
        val = input(f"  {_cyn('>')} {msg}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return val if val else default


def _pick(items, label_key, extra_keys=None):
    for i, item in enumerate(items):
        label = item.get(label_key) if isinstance(item, dict) else getattr(item, label_key, str(item))
        extra = ""
        if extra_keys and isinstance(item, dict):
            parts = [f"{k}={item.get(k, '')}" for k in extra_keys if item.get(k)]
            extra = f"  {_dim(', '.join(parts))}" if parts else ""
        print(f"    {_cyn(str(i+1).rjust(3))}  {_bold(label)}{extra}")
    print()
    val = _prompt(f"Pick [1-{len(items)}] or Enter to cancel")
    if not val:
        return None
    try:
        idx = int(val) - 1
        if 0 <= idx < len(items):
            return items[idx]
    except ValueError:
        pass
    print(f"    {_err('Invalid choice')}\n")
    return None


def _token_status_str(client):
    """Return a formatted string showing token status."""
    token = client.token_mgr.get()
    if not token:
        return f"{_warn('Not logged in')}"
    # Try JWT expiry first
    left = jwt_time_left(token)
    if left > 0:
        if left < REFRESH_MARGIN:
            return f"{_warn(f'Token expiring soon ({int(left)}s left)')}"
        h = int(left // 3600)
        m = int((left % 3600) // 60)
        return f"{_ok(f'Logged in')}  {_dim(f'(expires in {h}h {m}m)')}"
    # Fallback: timestamp-based (24h from save)
    try:
        import json
        with open(client.token_mgr.token_file) as f:
            data = json.load(f)
        ts = data.get("timestamp", 0)
        age_hours = (time.time() - ts) / 3600
        if age_hours < 24:
            remaining = 24 - age_hours
            h = int(remaining)
            m = int((remaining - h) * 60)
            return f"{_ok('Logged in')}  {_dim(f'(~{h}h {m}m remaining)')}"
    except Exception:
        pass
    return f"{_ok('Logged in')}"


def do_status(args, client):
    """Show current token and session status."""
    token = client.token_mgr.get()
    if not token:
        print(f"  {_warn('Not logged in')}")
        print(f"  {_dim('Run: lmsadwl login')}\n")
        return

    claims = None
    from .utils import parse_jwt
    claims = parse_jwt(token)

    left = jwt_time_left(token) if claims else 0

    print(f"  {_ok('Logged in')}\n")
    if claims:
        print(f"  {_dim('Token type:')}     {claims.get('typ', 'JWT')}")
        print(f"  {_dim('User ID:')}        {claims.get('sub', claims.get('userId', '-'))}")
    else:
        print(f"  {_dim('Token type:')}     Opaque session token")

    if claims and claims.get("exp"):
        exp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(claims["exp"]))
        print(f"  {_dim('Expires at:')}     {exp_str}")
    if left > 0:
        h = int(left // 3600)
        m = int((left % 3600) // 60)
        print(f"  {_dim('Time left:')}      {h}h {m}m")
    else:
        # Fallback: show timestamp-based expiry
        import json
        try:
            with open(client.token_mgr.token_file) as f:
                data = json.load(f)
            ts = data.get("timestamp", 0)
            age_hours = (time.time() - ts) / 3600
            if age_hours < 24:
                remaining = 24 - age_hours
                h = int(remaining)
                m = int((remaining - h) * 60)
                print(f"  {_dim('Session age:')}    {age_hours:.1f}h (refreshes in ~{h}h {m}m)")
            else:
                print(f"  {_warn('Session expired')}  ({age_hours:.1f}h old)")
        except Exception:
            print(f"  {_dim('Expires:')}       ~24h from login")

    if left < REFRESH_MARGIN and left > 0:
        print(f"  {_warn('Token will auto-refresh on next API call')}")


def _print_lookup(result):
    if result.found:
        for rom in result.roms:
            print(f"  {_ok('FOUND')}  {_bold(rom.name)}\n")
            if rom.publish_date:
                print(f"  {_dim('Published:')}    {rom.publish_date}")
            if rom.md5:
                print(f"  {_dim('MD5:')}         {rom.md5}")
            if rom.rom_match_id:
                print(f"  {_dim('Match ID:')}    {rom.rom_match_id}")
            if rom.uri:
                print(f"  {_dim('Download:')}    {_cyn(rom.uri[:90])}")
                if len(rom.uri) > 90:
                    print(f"  {_dim('               ')}{_cyn(rom.uri[90:])}")
            if rom.flash_flow:
                print(f"  {_dim('Flash flow:')}  {MAG}{rom.flash_flow[:80]}{RST}")
            print()
    elif result.rescue_available:
        print(f"  {_dim('INFO')}  Rescue data available (Rescue Lite mode)\n")
    elif result.code == "1000":
        print(f"  {_err('Not found')}  {_dim(result.desc)}\n")
    else:
        print(f"  {_err('Error')}  {result.desc} {_dim(f'(code: {result.code})')}\n")


def _download_rom(rom, output_dir, client=None):
    name = rom.name if hasattr(rom, "name") else rom.get("name", "")
    uri = rom.uri if hasattr(rom, "uri") else rom.get("uri", "")
    md5 = rom.md5 if hasattr(rom, "md5") else rom.get("md5", "")

    out_dir = os.path.abspath(output_dir)
    os.makedirs(out_dir, exist_ok=True)
    fname = name + (".zip" if not name.endswith(".zip") else "")
    out_file = os.path.join(out_dir, fname)

    print(f"  {_ok('ROM')}      {_bold(name)}")
    if md5:
        print(f"  {_dim('MD5:')}       {md5}")
    print(f"  {_dim('Target:')}    {out_file}\n")

    if os.path.exists(out_file):
        print(f"  {_warn('Already exists, skipping')}\n")
        return True

    try:
        print(f"  {_dim('Downloading...')}\n")
        r = requests.get(uri, stream=True, timeout=300)
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        start = time.time()

        with open(out_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start
                    print(f"\r  {_bar(downloaded, total, elapsed)}    ", end="", flush=True)

        elapsed = time.time() - start
        print(f"\n")

        if md5:
            print(f"  {_dim('Verifying MD5...')}")
            h = hashlib.md5()
            with open(out_file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            if h.hexdigest().upper() == md5.upper():
                print(f"  {_ok('MD5 OK')}  {h.hexdigest().upper()}\n")
            else:
                print(f"  {_err('MD5 MISMATCH')}\n")

        size_mb = os.path.getsize(out_file) / 1024 / 1024
        print(f"  {_ok('DONE')}  {size_mb:.1f} MB in {elapsed:.1f}s")
        print(f"  {_dim(out_file)}\n")
        return True

    except Exception as e:
        print(f"\n  {_err('FAILED')}  {e}\n")
        if os.path.exists(out_file):
            os.remove(out_file)
        return False


def do_login(args, client):
    if args.url:
        ok = client.login_with_url(args.url)
        if ok:
            print(f"\n  {_ok('OK')}  Token saved\n")
        else:
            print(f"\n  {_err('ERROR')}  Failed to exchange code\n")
        return

    if args.token:
        client.login_with_token(args.token)
        print(f"\n  {_ok('OK')}  Token saved\n")
        return

    url, state = client.login_interactive()
    if not url:
        print(f"\n  {_err('ERROR')}  Failed to get login URL\n")
        return

    print()
    print(f"  {YLW}+{RST}{'-'*52}{YLW}+{RST}")
    print(f"  {YLW}|{RST}  {_bold('Step 1:')}  Open this URL in browser {_dim('(JS disabled!)')}  {YLW}|{RST}")
    print(f"  {YLW}|{RST}                                                  {YLW}|{RST}")
    print(f"  {YLW}|{RST}  {_cyn(url)}")
    print(f"  {YLW}|{RST}                                                  {YLW}|{RST}")
    print(f"  {YLW}|{RST}  {_bold('Step 2:')}  Log in with your Lenovo account         {YLW}|{RST}")
    print(f"  {YLW}|{RST}                                                  {YLW}|{RST}")
    print(f"  {YLW}|{RST}  {_bold('Step 3:')}  Copy the full URL from address bar     {YLW}|{RST}")
    print(f"  {YLW}|{RST}                                                  {YLW}|{RST}")
    print(f"  {YLW}|{RST}  {_dim('Then run: lmsadwl login --url URL')}  {YLW}|{RST}")
    print(f"  {YLW}+{RST}{'-'*52}{YLW}+{RST}")
    print()


def do_models(args, client):
    keyword = getattr(args, "filter", "") or ""
    print(f"  {_dim('Fetching models')} ...\n")
    models = client.get_models(keyword=keyword, read_only=getattr(args, "read_only", False))

    if not models:
        print(f"  {_warn('No models found')}\n")
        return

    print(f"  {_ok(str(len(models)))} models found\n")
    print(f"  {DIM}{'BRAND':12s} {'MODEL':20s} {'MARKET NAME':35s} {'PLAT':6s}{RST}")
    print(f"  {DIM}{'─'*12} {'─'*20} {'─'*35} {'─'*6}{RST}")

    for m in models:
        badge = f"{BG_GRN}{WHT} RS{RST}" if m.read_support else f"{DIM}   {RST}"
        market = m.market_name[:30] + "..." if len(m.market_name) > 33 else m.market_name
        print(f"  {badge} {m.brand:12s} {_bold(m.model_name):20s} {market:35s} {_cyn(m.platform):6s}")
    print()


def do_search(args, client):
    print(f"  {_dim('Fetching ROM catalog')} ...\n")
    roms = client.search_roms(args.model)

    if not roms:
        print(f"  {_warn('No ROMs matching')} {_bold(args.model)}\n")
        return

    print(f"  {_ok(str(len(roms)))} ROMs matching {_bold(args.model)}\n")
    print(f"  {DIM}{'#':>3s}  {'ROM NAME':45s}  {'MD5':12s}{RST}")
    print(f"  {DIM}{'─'*3}  {'─'*45}  {'─'*12}{RST}")

    for i, rom in enumerate(roms):
        name = rom.name[:40] + "..." if len(rom.name) > 43 else rom.name
        md5 = (rom.md5[:12] + "...") if rom.md5 else "-"
        print(f"  {_cyn(str(i+1).rjust(3))}  {name:45s}  {_dim(md5):12s}")
    print()


def do_lookup(args, client):
    if args.sn:
        sn = args.sn.upper()
        if not is_valid_sn(sn):
            print(f"\n  {_err('Invalid serial')} {_dim('(8 chars, no I/O/L)')}\n")
            return
        print(f"  {_dim('Looking up')} {_bold(sn)} ...\n")
        result = client.lookup_by_sn(sn)
        _print_lookup(result)

    elif args.imei:
        if not is_valid_imei(args.imei):
            print(f"\n  {_err('Invalid IMEI')} {_dim('(14-15 digits)')}\n")
            return
        print(f"  {_dim('Looking up')} {_bold(args.imei)} ...\n")
        result = client.lookup_by_imei(args.imei, model_code=args.model_code, carrier=args.ro_carrier)
        _print_lookup(result)
    else:
        print(f"  {_err('Specify --sn or --imei')}\n")


def do_device(args, client):
    from .utils import has_adb, get_connected_devices, read_device_props
    if not has_adb():
        print(f"  {_err('ADB not found')} {_dim('Install: pkg install android-tools')}\n")
        return
    devices = get_connected_devices()
    if not devices:
        print(f"  {_warn('No devices connected')}\n")
        return
    for dev in devices:
        props = read_device_props(dev["serial"])
        print(f"\n  {_bold(props.title)}")
        print(f"  {_dim('Serial:')}     {props.serial}")
        print(f"  {_dim('Model:')}      {props.model}")
        print(f"  {_dim('Brand:')}      {props.brand}")
        print(f"  {_dim('Fingerprint:')} {_cyn(props.fingerprint[:60])}")
        print(f"  {_dim('Incremental:')} {props.incremental}")
    print()


def do_auto(args, client):
    from .utils import has_adb
    if not has_adb():
        print(f"  {_err('ADB not found')}\n")
        return
    print(f"  {_dim('Detecting device...')}\n")
    props, roms = client.auto_detect()
    if not props:
        print(f"  {_warn('No device connected')}\n")
        return
    print(f"  {_dim('Detected:')} {_bold(props.title)}")
    print(f"  {_dim('Serial:')}   {props.serial}\n")

    if roms:
        print(f"  {_ok(str(len(roms)))} ROMs found\n")
        rom = _pick(roms, "name", ["md5"])
        if rom:
            _download_rom(rom, "./download")
    else:
        print(f"  {_warn('No ROM found')}\n")


def do_download(args, client):
    print(f"  {_dim('Fetching ROM catalog')} ...\n")
    roms = client.search_roms(args.model)
    if not roms:
        print(f"  {_warn('No ROMs matching')} {_bold(args.model)}\n")
        return
    if len(roms) > 1:
        print(f"  {_warn(f'{len(roms)} matches, using first')}\n")
    _download_rom(roms[0], args.output)


def do_batch(args, client):
    print(f"  {_dim('Fetching ROM catalog')} ...\n")
    roms = client.search_roms(args.model)
    if not roms:
        print(f"  {_warn('No ROMs matching')} {_bold(args.model)}\n")
        return
    if args.limit > 0:
        roms = roms[:args.limit]
    print(f"  {_ok(str(len(roms)))} ROMs matching {_bold(args.model)}\n")
    for i, rom in enumerate(roms):
        print(f"  {_cyn(f'[{i+1}/{len(roms)}]')}  {_bold(rom.name)}")
        _download_rom(rom, args.output)
    print(f"  {_ok('Batch complete')}  {_dim(args.output)}\n")


def do_interactive(args, client):
    os.system("clear" if os.name != "nt" else "cls")
    banner()
    print(f"  {_token_status_str(client)}\n")

    while True:
        print(f"""
  {CYN}{BOLD}┌──────────────────────────────────────────────────────┐
  │                                                      │
  │   {WHT}Select an action:{CYN}                                    │
  │                                                      │
  │   {GRN}1{RST}  {_bold('Auto-detect device')}  {_dim('(via ADB)')                  }│
  │   {GRN}2{RST}  {_bold('Detect device')}        {_dim('(show info only)')             }│
  │   {GRN}3{RST}  {_bold('Lookup by SN')}        {_dim('(serial number)')              }│
  │   {GRN}4{RST}  {_bold('Lookup by IMEI')}       {_dim('(14-15 digits)')              }│
  │   {GRN}5{RST}  {_bold('Search ROMs')}         {_dim('(by model name)')             }│
  │   {GRN}6{RST}  {_bold('List models')}         {_dim('(supported devices)')         }│
  │   {GRN}7{RST}  {_bold('Download ROM')}        {_dim('(single file)')               }│
  │   {GRN}8{RST}  {_bold('Batch download')}      {_dim('(all matches)')               }│
  │   {GRN}9{RST}  {_bold('Login')}               {_dim('(Lenovo account)')             }│
  │   {GRN}s{RST}  {_bold('Token status')}        {_dim('(show expiry info)')           }│
  │   {GRN}0{RST}  {_dim('Exit')}                                                  │
  │                                                      │
  └──────────────────────────────────────────────────────┘{RST}
""")
        choice = _prompt("Choice [0-9]", "0")
        if choice in ("0", None):
            print(f"\n  {_dim('Bye!')}\n")
            break

        elif choice == "1":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Auto-detect device')}\n")
            props, roms = client.auto_detect()
            if not props:
                print(f"  {_warn('No device connected')}\n")
                _prompt("Press Enter")
                continue
            print(f"  {_dim('Detected:')} {_bold(props.title)}")
            print(f"  {_dim('Serial:')}   {props.serial}\n")
            if roms:
                print(f"  {_ok(str(len(roms)))} ROMs found\n")
                rom = _pick(roms, "name", ["md5"])
                if rom:
                    _download_rom(rom, "./download")
            else:
                print(f"  {_warn('No ROM found')}\n")
            _prompt("Press Enter")

        elif choice == "2":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Detect device')}\n")
            do_device(args, client)
            _prompt("Press Enter")

        elif choice == "3":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Lookup by serial number')}\n")
            sn = _prompt("Serial number (8 chars)")
            if not sn:
                _prompt("Press Enter")
                continue
            args.sn = sn.upper()
            args.imei = None
            do_lookup(args, client)
            _prompt("Press Enter")

        elif choice == "4":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Lookup by IMEI')}\n")
            imei = _prompt("IMEI (14-15 digits)")
            if not imei:
                _prompt("Press Enter")
                continue
            args.imei = imei
            args.sn = None
            args.model_code = None
            args.ro_carrier = None
            do_lookup(args, client)
            _prompt("Press Enter")

        elif choice == "5":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Search ROMs')}\n")
            keyword = _prompt("Model name or keyword")
            if not keyword:
                _prompt("Press Enter")
                continue
            args.model = keyword
            do_search(args, client)
            _prompt("Press Enter")

        elif choice == "6":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('List models')}\n")
            keyword = _prompt("Filter keyword (or Enter for all)")
            args.filter = keyword
            args.read_only = False
            do_models(args, client)
            _prompt("Press Enter")

        elif choice == "7":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Download ROM')}\n")
            keyword = _prompt("Search for ROM")
            if not keyword:
                _prompt("Press Enter")
                continue
            args.model = keyword
            args.output = "./download"
            do_download(args, client)
            _prompt("Press Enter")

        elif choice == "8":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Batch download')}\n")
            keyword = _prompt("Search keyword")
            if not keyword:
                _prompt("Press Enter")
                continue
            limit = _prompt("Max ROMs (0=all)", "0")
            try:
                limit = int(limit) if limit else 0
            except ValueError:
                limit = 0
            args.model = keyword
            args.output = "./download"
            args.limit = limit
            do_batch(args, client)
            _prompt("Press Enter")

        elif choice == "9":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Login')}\n")
            login_args = argparse.Namespace(url=None, token=None)
            do_login(login_args, client)
            _prompt("Press Enter")

        elif choice == "s":
            os.system("clear" if os.name != "nt" else "cls")
            print(f"\n  {_bold('Token status')}\n")
            do_status(args, client)
            _prompt("Press Enter")

        else:
            print(f"  {_err('Invalid choice')}\n")
            _prompt("Press Enter")


def main():
    parser = argparse.ArgumentParser(
        prog="lmsadwl",
        description=f"{CYN}Lenovo LMSA ROM Downloader{RST}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""{_dim('commands:')}
  login        Login via Lenovo Passport OAuth2
  models       List supported device models
  search       Search ROMs by model name
  lookup       Find firmware by serial number or IMEI
  device       Detect connected device via ADB
  auto         Auto-detect device and find firmware
  download     Download a ROM package
  batch        Download multiple ROMs by keyword
  status       Show token status and expiry
  shell        Interactive mode menu

{_dim('examples:')}
  {_bold('lmsadwl login')}
  {_bold('lmsadwl status')}                        {_dim('# show token expiry')}
  {_bold('lmsadwl shell')}                          {_dim('# interactive mode')}
  {_bold('lmsadwl auto')}                          {_dim('# auto-detect + lookup')}
  {_bold('lmsadwl lookup --sn HA1CQ8SV')}
  {_bold('lmsadwl search --model x606')}
  {_bold('lmsadwl models --read-only -f moto')}
  {_bold('lmsadwl download --model TB-X606X_S301085')}
  {_bold('lmsadwl batch --model x606')}            {_dim('# download all matches')}
""",
    )

    sub = parser.add_subparsers(dest="command")

    sp_login = sub.add_parser("login", help="Login via Lenovo Passport OAuth2")
    sp_login.add_argument("--url", "-u", help="Paste redirect URL from browser")
    sp_login.add_argument("--token", "-t", help="Paste token directly")

    sp_models = sub.add_parser("models", help="List all available models")
    sp_models.add_argument("--filter", "-f", default="", help="Filter by keyword")
    sp_models.add_argument("--read-only", "-r", action="store_true")

    sp_search = sub.add_parser("search", help="Search for ROMs by model name")
    sp_search.add_argument("--model", "-m", required=True)

    sp_lookup = sub.add_parser("lookup", help="Lookup firmware by SN or IMEI")
    sp_lookup.add_argument("--sn", help="Serial number (8 chars)")
    sp_lookup.add_argument("--imei", help="IMEI (14-15 digits)")
    sp_lookup.add_argument("--model-code", help="Model code (IMEI lookup)")
    sp_lookup.add_argument("--ro-carrier", help="Carrier (IMEI lookup)")

    sp_dl = sub.add_parser("download", help="Download a ROM package")
    sp_dl.add_argument("--model", "-m", required=True)
    sp_dl.add_argument("-o", "--output", default="./download")

    sub.add_parser("device", help="Detect connected device via ADB")
    sub.add_parser("auto", help="Auto-detect device and find firmware")

    sp_batch = sub.add_parser("batch", help="Download all ROMs matching keyword")
    sp_batch.add_argument("--model", "-m", required=True)
    sp_batch.add_argument("-o", "--output", default="./download")
    sp_batch.add_argument("--limit", "-n", type=int, default=0)

    sub.add_parser("shell", help="Interactive mode menu")
    sub.add_parser("i", help="Interactive mode (alias)")
    sub.add_parser("status", help="Show token status and expiry")

    args = parser.parse_args()
    if not args.command:
        banner()
        parser.print_help()
        return

    client = LMSAClient()
    cmd_map = {
        "login": do_login,
        "models": do_models,
        "search": do_search,
        "lookup": do_lookup,
        "device": do_device,
        "auto": do_auto,
        "download": do_download,
        "batch": do_batch,
        "shell": do_interactive,
        "i": do_interactive,
        "status": do_status,
    }

    try:
        cmd_map[args.command](args, client)
    except AuthError as e:
        print(f"\n  {_err('AUTH ERROR')}  {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
