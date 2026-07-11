"""Token management and OAuth2 authentication with auto-refresh."""

import json
import os
import time
import uuid
from base64 import b64encode
from urllib.parse import urlparse, parse_qs

import requests

from .endpoints import EP_GET_API_INFO, EP_INIT_TOKEN, EP_OAUTH2_CALLBACK, EP_SF_USER_INFO
from .utils import jwt_is_expired, jwt_time_left, parse_jwt

API_BASE = "https://lsa.lenovo.com/Interface"
CLIENT_VERSION = "7.5.5.19"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/51.0.2704.79 Safari/537.36"
)
WIN_INFO = "Microsoft Windows 10 Pro, 64-bit"

REFRESH_MARGIN = 300  # 5 minutes before expiry, treat as expired


class TokenManager:
    """Manages token storage, loading, validation, and auto-relogin.

    Token file stores: { token, timestamp, expires_at }

    Passive refresh: after every API call, check the response's Authorization
    header. If the server returned a new token, save it immediately.
    """

    def __init__(self, token_dir=None, auto_relogin=True, max_retries=2):
        if token_dir is None:
            token_dir = os.path.join(os.path.expanduser("~"), ".lmsadwl")
        self.token_dir = token_dir
        self.token_file = os.path.join(token_dir, "token.json")
        self.auto_relogin = auto_relogin
        self.max_retries = max_retries
        self._token = None
        self._on_relogin = None  # callback: () -> token or None
        self._last_oauth_session = None  # saved OAuth session for auto-retry

    def set_relogin_callback(self, callback):
        """Set a callback function called when relogin is needed.
        The callback should perform the OAuth2 flow and return a token string or None.
        """
        self._on_relogin = callback

    def load(self):
        if os.path.exists(self.token_file):
            try:
                with open(self.token_file) as f:
                    data = json.load(f)
                token = data.get("token")
                if not token:
                    return None

                # Check expiry from JWT claims first (proactive)
                if not jwt_is_expired(token, margin_seconds=REFRESH_MARGIN):
                    self._token = token
                    return self._token

                # Fallback: check timestamp-based expiry (24h)
                ts = data.get("timestamp", 0)
                if (time.time() - ts) / 3600 < 24:
                    self._token = token
                    return self._token

                # Token expired by all checks
                return None
            except (json.JSONDecodeError, KeyError):
                pass
        # Try migrating from old token location
        migrated = self._migrate_old_token()
        if migrated:
            return migrated
        return None

    def _migrate_old_token(self):
        old_paths = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".lmsa_tokens.json"),
            os.path.join(os.getcwd(), ".lmsa_tokens.json"),
        ]
        for old_path in old_paths:
            old_path = os.path.normpath(old_path)
            if os.path.exists(old_path):
                try:
                    with open(old_path) as f:
                        data = json.load(f)
                    token = data.get("token")
                    ts = data.get("timestamp", 0)
                    if token and (time.time() - ts) / 3600 < 24:
                        self.save(token)
                        return token
                except (json.JSONDecodeError, KeyError):
                    pass
        return None

    def save(self, token):
        self._token = token
        os.makedirs(self.token_dir, exist_ok=True)
        exp = jwt_time_left(token)
        with open(self.token_file, "w") as f:
            json.dump({
                "token": token,
                "timestamp": time.time(),
                "expires_in": int(exp) if exp else None,
                "expires_at": int(time.time() + exp) if exp else None,
            }, f, indent=2)

    def get(self):
        """Get a valid token, checking JWT expiry proactively."""
        if self._token and not jwt_is_expired(self._token, margin_seconds=REFRESH_MARGIN):
            return self._token
        self._token = None
        return self.load()

    def is_expired(self):
        if not self._token:
            return True
        return jwt_is_expired(self._token, margin_seconds=REFRESH_MARGIN)

    def time_left(self):
        """Seconds until token expires."""
        if not self._token:
            return 0
        return jwt_time_left(self._token)

    def clear(self):
        self._token = None
        if os.path.exists(self.token_file):
            os.remove(self.token_file)

    def _validate_token(self, token):
        s = _make_session()
        guid = str(uuid.uuid4())
        hdrs = _make_headers(guid, token)
        try:
            r = s.get(EP_SF_USER_INFO, headers=hdrs, timeout=10)
            data = r.json()
            return data.get("code") == "0000"
        except Exception:
            return False

    def refresh_from_response(self, response):
        """Passive token refresh: check Authorization header in API response.

        If the server returned a new token in the response headers, save it.
        This is how the TypeScript version keeps tokens alive — every response
        can carry an updated token.
        """
        try:
            auth_header = response.headers.get("Authorization", "")
            if not auth_header:
                return
            new_token = auth_header
            if new_token.lower().startswith("bearer "):
                new_token = new_token[7:]
            if not new_token or new_token == self._token:
                return
            # Validate the new token before saving
            if self._validate_token(new_token):
                self.save(new_token)
        except Exception:
            pass

    def ensure_valid(self):
        """Ensure we have a valid token. Returns token or triggers relogin.

        Priority:
        1. Return cached token if not expired (JWT check)
        2. Load from disk if not expired
        3. Validate with server (getSFUserInfo)
        4. Try relogin callback
        5. Return None
        """
        # 1. Check cached token
        if self._token and not jwt_is_expired(self._token, margin_seconds=REFRESH_MARGIN):
            if self._validate_token(self._token):
                return self._token
            # Token looks expired to server, force relogin
            self._token = None

        # 2. Try loading from disk
        token = self.load()
        if token and self._validate_token(token):
            return token

        # 3. Token is invalid/expired — relogin
        if self.auto_relogin:
            return self._do_relogin()
        return None

    def _do_relogin(self):
        """Attempt relogin up to max_retries times."""
        for attempt in range(self.max_retries):
            if self._on_relogin:
                token = self._on_relogin()
                if token and self._validate_token(token):
                    self.save(token)
                    return token
            return None
        return None

    def save_oauth_session(self, session):
        """Save the OAuth HTTP session for potential auto-reuse."""
        self._last_oauth_session = session

    def get_oauth_session(self):
        """Get the saved OAuth HTTP session."""
        return self._last_oauth_session


class OAuth2Flow:
    """Handles the OAuth2 PKCE login flow."""

    def __init__(self):
        self._init_session = None

    def get_login_url(self):
        """Step 1: Get the OAuth2 authorization URL."""
        s = _make_session()
        self._init_session = s
        guid = str(uuid.uuid4())
        hdrs = _make_headers(guid)
        r = s.post(
            EP_GET_API_INFO,
            json=_wrap_body({"key": "TIP_URL"}),
            headers=hdrs,
            timeout=30,
        )
        data = r.json()
        if data.get("code") != "0000":
            return None, None
        url = data["content"]
        state = parse_qs(urlparse(url).query)["state"][0]
        return url, state

    def exchange_code(self, code, state):
        """Exchange an authorization code for a token."""
        s = _make_session()
        guid = str(uuid.uuid4())
        params = {"code": code, "scope": "openid", "state": state}
        hdrs = _make_headers(guid)
        r = s.get(
            EP_OAUTH2_CALLBACK,
            params=params,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://lsa.lenovo.com/Tips/lenovoIdSuccess.html",
                "Request-Tag": "lmsa",
                "Guid": guid,
                "clientVersion": CLIENT_VERSION,
                "windowsInfo": b64encode(WIN_INFO.encode()).decode(),
                "language": "en-US",
            },
            timeout=30,
            allow_redirects=False,
        )
        data = r.json()
        content = data.get("content", "") or data.get("desc", "")
        if "softwareFix://" not in content:
            return None
        inner = content.split("?", 1)[1] if "?" in content else ""
        p = parse_qs(inner)
        token = p.get("Authorization", [None])[0]
        if token:
            self._init_after_login(s, guid, token)
        return token

    def _init_after_login(self, session, guid, token):
        """Post-login initialization: call RSA and initToken endpoints."""
        try:
            hdrs = _make_headers(guid, token)
            session.get(
                "https://lsa.lenovo.com/Interface/common/rsa.jhtml",
                headers=hdrs,
                timeout=15,
            )
            session.post(
                EP_INIT_TOKEN,
                json=_wrap_body({}),
                headers=hdrs,
                timeout=15,
            )
        except Exception:
            pass

    def login_step1(self):
        url, state = self.get_login_url()
        return url, state

    def login_step2(self, redirect_url):
        """Exchange redirect URL for token."""
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        if not code:
            return None
        return self.exchange_code(code, state)


def _make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    s.get("https://lsa.lenovo.com/lmsa-web/index.jsp", timeout=30, allow_redirects=True)
    return s


def _make_headers(guid, token=None):
    h = {
        "User-Agent": USER_AGENT,
        "Cache-Control": "no-cache",
        "Request-Tag": "lmsa",
        "Content-Type": "application/json",
        "Guid": guid,
        "clientVersion": CLIENT_VERSION,
        "language": "en-US",
        "windowsInfo": b64encode(WIN_INFO.encode()).decode(),
    }
    if token:
        h["Authorization"] = token if token.startswith("Bearer ") else "Bearer " + token
    return h


def _wrap_body(params=None):
    return {
        "client": {"version": CLIENT_VERSION},
        "language": "en-US",
        "windowsInfo": WIN_INFO,
        "dparams": params,
    }
