# lmsadwl

Lenovo LMSA ROM Downloader — download firmware from official Lenovo servers.

Works with Lenovo Software Fix (LMSA) v7.5.5.19 API. No third-party servers, only official Lenovo CDN.

## Features

- **Lookup by serial number** — find exact firmware for your device
- **Lookup by IMEI** — alternative device identification
- **Auto-detect** — connect device via USB/ADB, read serial, find firmware automatically
- **Search catalog** — browse all 2400+ ROMs in Lenovo's database
- **Download with progress** — ETA, speed, MD5 verification
- **Batch download** — download all ROMs matching a keyword
- **Auto-relogin** — token refresh on auth errors (403/408)
- **Interactive mode** — menu-driven interface for non-CLI users
- **Python library** — use `LMSAClient` in your own code

## Install

```bash
git clone https://github.com/YOUR_USERNAME/lmsadwl.git
cd lmsadwl
pip install -e .
```

## Usage

### CLI

```bash
# Login
lmsadwl login

# Lookup firmware by serial number
lmsadwl lookup --sn HA1CQ8SV

# Auto-detect connected device
lmsadwl auto

# Search ROMs
lmsadwl search --model x606

# List supported models
lmsadwl models --filter moto

# Download a ROM
lmsadwl download --model TB-X606X_S301085

# Batch download
lmsadwl batch --model x606 --limit 5

# Interactive mode
lmsadwl shell
```

### Python

```python
from lmsadwl import LMSAClient

client = LMSAClient()

# Search ROMs
roms = client.search_roms("x606")
for rom in roms:
    print(rom.name, rom.uri)

# Lookup by serial number
result = client.lookup_by_sn("HA1CQ8SV")
if result.found:
    rom = result.roms[0]
    print(rom.name, rom.uri)

# Auto-detect device via ADB
device, roms = client.auto_detect()
if device:
    print(device.title, device.serial)

# Models
models = client.get_models(keyword="moto", read_only=True)
for m in models:
    print(m.model_name, m.market_name)
```

### Auto-relogin

```python
def my_relogin():
    """Called when token expires. Return new token or None."""
    url, state = client.login_interactive()
    print(f"Open: {url}")
    redirect = input("Paste redirect URL: ")
    return client.login_with_url(redirect)

client.set_relogin_callback(my_relogin)

# Now any API call will auto-relogin on 403/408
roms = client.search_roms("x606")
```

## Login flow

The tool uses Lenovo's OAuth2 PKCE flow:

1. Run `lmsadwl login` — get a login URL
2. Open URL in browser (disable JavaScript first!)
3. Log in with your Lenovo account
4. Copy the redirect URL from the address bar
5. Run `lmsadwl login --url "PASTE_URL"`

Token is stored in `~/.lmsadwl/token.json` (24h expiry).

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `getRomList.jhtml` | POST | Full ROM catalog (2400+) |
| `getModelNames.jhtml` | POST | Supported device models |
| `getNewResourceBySN.jhtml` | POST | Lookup by serial number |
| `getNewResourceByImei.jhtml` | POST | Lookup by IMEI |
| `getRomMatchParams.jhtml` | POST | Required params for device |
| `getSFUserInfo.jhtml` | GET | Token validation |
| `getApiInfo.jhtml` | POST | OAuth2 URL generation |

## License

MIT
