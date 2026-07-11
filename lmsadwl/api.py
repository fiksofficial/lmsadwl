"""Core LMSA API client with automatic token refresh on auth errors."""

import time
import uuid

import requests

from .auth import (
    OAuth2Flow,
    TokenManager,
    CLIENT_VERSION,
    USER_AGENT,
    WIN_INFO,
    _make_headers,
    _make_session,
    _wrap_body,
)
from .endpoints import (
    EP_MODEL_NAMES,
    EP_NEW_RESOURCE,
    EP_NEW_RESOURCE_IMEI,
    EP_NEW_RESOURCE_SN,
    EP_ROM_LIST,
    EP_ROM_MATCH,
)
from .models import DeviceInfo, LookupResult, ModelInfo, ROMInfo
from .utils import is_valid_imei, is_valid_sn


class AuthError(Exception):
    pass


class LMSAClient:
    """Lenovo LMSA API client with automatic token refresh.

    Token lifecycle:
    1. On first use, load token from disk
    2. Before each API call, check JWT expiry proactively
    3. If token is expired/near-expiry, trigger relogin
    4. If API returns 403/408, retry with relogin
    """

    def __init__(self, token_dir=None, auto_relogin=True, max_retries=2):
        self.token_mgr = TokenManager(
            token_dir=token_dir, auto_relogin=auto_relogin, max_retries=max_retries
        )
        self.oauth = OAuth2Flow()
        self.max_retries = max_retries
        self._on_relogin = None

    def set_relogin_callback(self, callback):
        """Set a callback for interactive relogin.
        The callback should perform the OAuth2 flow and return a token string.
        """
        self._on_relogin = callback
        self.token_mgr.set_relogin_callback(callback)

    # ── Login methods ──

    def login_interactive(self):
        """Step 1: Get OAuth2 login URL."""
        url, state = self.oauth.login_step1()
        if not url:
            return None, None
        return url, state

    def login_with_url(self, redirect_url):
        """Step 2: Exchange redirect URL for token."""
        token = self.oauth.login_step2(redirect_url)
        if token:
            self.token_mgr.save(token)
            return True
        return False

    def login_with_token(self, token):
        """Save a token directly."""
        self.token_mgr.save(token)

    # ── Auth management ──

    def ensure_auth(self):
        """Ensure we have a valid token. Raises AuthError if relogin fails."""
        token = self.token_mgr.ensure_valid()
        if token:
            return token

        # Token invalid/expired — try automatic relogin
        token = self._try_relogin()
        if token:
            return token

        raise AuthError(
            "Not logged in or token expired.\n"
            "  Run: lmsadwl login\n"
            "  Or:  lmsadwl login --url URL"
        )

    def _validate_token(self, token):
        return self.token_mgr._validate_token(token)

    def _try_relogin(self):
        """Try to relogin. Uses callback if set, otherwise OAuth2Flow directly."""
        # 1. Try callback first
        if self._on_relogin:
            token = self._on_relogin()
            if token and self._validate_token(token):
                self.token_mgr.save(token)
                return token

        # 2. Fallback: interactive OAuth2 in terminal
        return self._interactive_relogin()

    def _interactive_relogin(self):
        """Automatic relogin via terminal OAuth2 flow."""
        import sys
        for attempt in range(self.max_retries):
            try:
                url, state = self.oauth.login_step1()
                if not url:
                    continue
                print(f"\n\033[33mToken expired. Re-login required.\033[0m")
                print(f"\033[36mOpen this URL:\033[0m\n  {url}\n")
                try:
                    redirect = input("  \033[36m>\033[0m Paste redirect URL: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    return None
                if not redirect:
                    continue
                token = self.oauth.login_step2(redirect)
                if token and self._validate_token(token):
                    self.token_mgr.save(token)
                    return token
                print("\033[31mInvalid token, try again.\033[0m")
            except Exception:
                continue
        return None

    # ── API call with auto-retry on auth errors ──

    def _api_call(self, endpoint, params=None, token=None, method="post"):
        s = _make_session()
        guid = str(uuid.uuid4())
        hdrs = _make_headers(guid, token)
        if method == "get":
            r = s.get(endpoint, headers=hdrs, timeout=30)
        else:
            r = s.post(endpoint, json=_wrap_body(params), headers=hdrs, timeout=30)
        # Passive token refresh — server may return a new token in headers
        self.token_mgr.refresh_from_response(r)
        return r.json()

    def _call_with_retry(self, endpoint, params=None, method="post"):
        """Make an API call with automatic relogin on auth errors.

        Flow:
        1. Get valid token (may trigger relogin if expired)
        2. Make the API call (passive refresh from response headers)
        3. If 403/408/402, relogin and retry once
        """
        # Ensure auth (this handles proactive refresh)
        token = self.ensure_auth()
        result = self._api_call(endpoint, params=params, token=token, method=method)
        code = result.get("code", "?")

        # Auth error — try relogin and retry
        if code in ("403", "408", "402"):
            token = self._try_relogin()
            if token:
                result = self._api_call(endpoint, params=params, token=token, method=method)
        return result

    # ── Public API methods ──

    def get_rom_list(self):
        result = self._call_with_retry(EP_ROM_LIST, params={})
        if result.get("code") != "0000":
            return []
        return [ROMInfo.from_dict(r) for r in result.get("content", [])]

    def search_roms(self, keyword):
        roms = self.get_rom_list()
        return [r for r in roms if keyword.lower() in r.name.lower()]

    def get_models(self, keyword="", read_only=False):
        result = self._call_with_retry(EP_MODEL_NAMES, params={"keyword": keyword, "siteId": 54})
        if result.get("code") != "0000":
            return []
        models = [ModelInfo.from_dict(m) for m in result.get("content", {}).get("models", [])]
        if read_only:
            models = [m for m in models if m.read_support]
        return models

    def lookup_by_sn(self, sn):
        sn = sn.upper()
        if not is_valid_sn(sn):
            raise ValueError(f"Invalid serial number: {sn}")
        result = self._call_with_retry(EP_NEW_RESOURCE_SN, params={"sn": sn})
        return LookupResult.from_response(result)

    def lookup_by_imei(self, imei, model_code=None, carrier=None):
        if not is_valid_imei(imei):
            raise ValueError(f"Invalid IMEI: {imei}")
        dparams = {
            "imei": imei,
            "encryptCode": uuid.uuid4().hex[:8].upper(),
        }
        if model_code:
            dparams["modelCode"] = model_code
        if carrier:
            dparams["roCarrier"] = carrier
        result = self._call_with_retry(EP_NEW_RESOURCE_IMEI, params=dparams)
        return LookupResult.from_response(result)

    def auto_detect(self):
        from .utils import get_connected_devices, read_device_props

        devices = get_connected_devices()
        if not devices:
            return None, []

        dev = devices[0]
        props = read_device_props(dev["serial"])

        sn = props.serial or ""
        roms = []
        if sn and is_valid_sn(sn):
            lookup = self.lookup_by_sn(sn)
            if lookup.found:
                return props, lookup.roms

        if props.model:
            roms = self.search_roms(props.model)

        return props, roms

    def get_rom_match(self, model_name):
        result = self._call_with_retry(EP_ROM_MATCH, params={"modelName": model_name})
        if result.get("code") == "0000":
            return result.get("content")
        return None
