"""
Accounts, sign-in sessions, API keys and roles.

- People sign in with email + password (scrypt hashes). A session is a random
  token in an HttpOnly cookie; the database keeps only its SHA-256.
- Scanners and scripts use API keys (`Authorization: Bearer mc_<prefix>_<secret>`).
  A key acts with its owner's role; only a hash of the secret is stored.
- Roles: `viewer` reads everything; `admin` also uploads scans, regenerates
  QA plans, manages test templates, users and API keys.
- Requests that change something from a browser carry the session's CSRF
  token (form field `csrf_token` or header `X-CSRF-Token`).
"""
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, List, Optional

from sqlalchemy import delete, func, insert, select, update

from app import db
from app.config import SESSION_DAYS
from app.db import api_keys, sessions, users

ADMIN, VIEWER = "admin", "viewer"
ROLES = (ADMIN, VIEWER)
SESSION_COOKIE = "mc_session"
MIN_PASSWORD_LENGTH = 10
KEY_PREFIX = "mc"


class AuthError(Exception):
    """Something the person can fix; the message is safe to show."""


@dataclass
class Principal:
    """Who is making a request: a signed-in person or an API key."""
    id: int
    email: str
    name: str
    role: str
    must_change_password: bool = False
    via: str = "session"              # session | api_key
    csrf_token: Optional[str] = None
    key_name: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN


# ---------------------------------------------------------------- passwords

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=len(digest) // 2,
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual.hex(), digest)
    except (ValueError, TypeError):
        return False


# a fixed hash to compare against when the email is unknown, so timing does not reveal accounts
_DUMMY_HASH = hash_password(secrets.token_hex(8))


def check_password_rules(password: str):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    if (password or "").strip() != password:
        raise AuthError("The password cannot start or end with spaces.")


def generate_password() -> str:
    """A temporary password an admin hands over (changed at first sign-in)."""
    return secrets.token_urlsafe(12)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# ---------------------------------------------------------------- users

def _user(row) -> Optional[dict]:
    return dict(row._mapping) if row is not None else None


def count_users() -> int:
    with db.engine().connect() as conn:
        return conn.execute(select(func.count()).select_from(users)).scalar_one()


def get_user(user_id: int) -> Optional[dict]:
    with db.engine().connect() as conn:
        return _user(conn.execute(select(users).where(users.c.id == user_id)).first())


def get_user_by_email(email: str) -> Optional[dict]:
    with db.engine().connect() as conn:
        return _user(conn.execute(select(users).where(users.c.email == (email or "").strip().lower())).first())


def list_users() -> List[dict]:
    with db.engine().connect() as conn:
        return [_user(r) for r in conn.execute(select(users).order_by(users.c.email))]


def _clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 320 or " " in email:
        raise AuthError("Enter a valid email address.")
    return email


def _insert_user(conn, email, name, role, password, must_change_password) -> int:
    email = _clean_email(email)
    name = (name or "").strip() or email.split("@")[0]
    if role not in ROLES:
        raise AuthError("Unknown role.")
    check_password_rules(password)
    if conn.execute(select(users.c.id).where(users.c.email == email)).first():
        raise AuthError("A user with this email already exists.")
    return conn.execute(insert(users).values(
        email=email, name=name[:200], role=role, password_hash=hash_password(password), active=True,
        must_change_password=must_change_password, created_at=db.utcnow(),
    ).returning(users.c.id)).scalar_one()


def create_user(email: str, name: str, role: str, password: str, must_change_password: bool = False) -> dict:
    with db.engine().begin() as conn:
        user_id = _insert_user(conn, email, name, role, password, must_change_password)
    return get_user(user_id)


def create_first_admin(email: str, name: str, password: str) -> dict:
    """Only while there are no users at all (first run); atomic, so two setups cannot both win."""
    with db.engine().begin() as conn:
        with db.lock(conn, "first-admin"):
            if conn.execute(select(func.count()).select_from(users)).scalar_one():
                raise AuthError("This server is already set up. Sign in instead.")
            user_id = _insert_user(conn, email, name, ADMIN, password, False)
    return get_user(user_id)


def _active_admins(conn, excluding: Optional[int] = None) -> int:
    query = select(func.count()).select_from(users).where(users.c.role == ADMIN, users.c.active.is_(True))
    if excluding is not None:
        query = query.where(users.c.id != excluding)
    return conn.execute(query).scalar_one()


def update_user(user_id: int, role: Optional[str] = None, active: Optional[bool] = None, name: Optional[str] = None):
    """Change role / active state; the last active admin cannot be demoted or deactivated."""
    values = {}
    if role is not None:
        if role not in ROLES:
            raise AuthError("Unknown role.")
        values["role"] = role
    if active is not None:
        values["active"] = bool(active)
    if name is not None and name.strip():
        values["name"] = name.strip()[:200]
    if not values:
        return
    with db.engine().begin() as conn:
        with db.lock(conn, "admins"):
            target = conn.execute(select(users).where(users.c.id == user_id)).first()
            if target is None:
                raise AuthError("No such user.")
            loses_admin = target.role == ADMIN and target.active and (values.get("role", ADMIN) != ADMIN or values.get("active") is False)
            if loses_admin and _active_admins(conn, excluding=user_id) == 0:
                raise AuthError("There must be at least one active admin.")
            conn.execute(update(users).where(users.c.id == user_id).values(**values))
            if values.get("active") is False:
                conn.execute(delete(sessions).where(sessions.c.user_id == user_id))


def set_password(user_id: int, password: str, must_change: bool = False):
    check_password_rules(password)
    with db.engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(
            password_hash=hash_password(password), must_change_password=must_change))
        conn.execute(delete(sessions).where(sessions.c.user_id == user_id))     # sign out everywhere


def change_own_password(user_id: int, current: str, new: str):
    user = get_user(user_id)
    if user is None or not verify_password(current or "", user["password_hash"]):
        raise AuthError("The current password is not correct.")
    if current == new:
        raise AuthError("Choose a password different from the current one.")
    set_password(user_id, new, must_change=False)


# ---------------------------------------------------------------- sign-in throttling

class _Throttle:
    """Lock an email (or address) for a while after repeated failed sign-ins (per server process)."""
    LIMIT, WINDOW = 5, 15 * 60

    def __init__(self):
        self._failures: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> List[float]:
        cutoff = time.monotonic() - self.WINDOW
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return recent

    def blocked(self, *keys: str) -> bool:
        with self._lock:
            return any(len(self._recent(k)) >= self.LIMIT for k in keys)

    def failed(self, *keys: str):
        with self._lock:
            for k in keys:
                self._recent(k).append(time.monotonic())

    def reset(self, *keys: str):
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)


throttle = _Throttle()


def authenticate(email: str, password: str, address: str = "") -> dict:
    email = (email or "").strip().lower()
    keys = (f"email:{email}", f"ip:{address}")
    if throttle.blocked(*keys):
        raise AuthError("Too many failed attempts. Wait 15 minutes and try again.")
    user = get_user_by_email(email)
    valid = verify_password(password or "", user["password_hash"] if user else _DUMMY_HASH)
    if not user or not valid or not user["active"]:
        throttle.failed(*keys)
        raise AuthError("Email or password is not correct.")
    throttle.reset(*keys)
    return user


# ---------------------------------------------------------------- sessions

def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = db.utcnow()
    with db.engine().begin() as conn:
        conn.execute(delete(sessions).where(sessions.c.expires_at < now))
        conn.execute(update(users).where(users.c.id == user_id).values(last_login_at=now))
        conn.execute(insert(sessions).values(
            token_hash=_sha256(token), user_id=user_id, csrf_token=secrets.token_urlsafe(24),
            created_at=now, expires_at=now + timedelta(days=SESSION_DAYS)))
    return token


def end_session(token: Optional[str]):
    if token:
        with db.engine().begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.token_hash == _sha256(token)))


def principal_from_session(token: Optional[str]) -> Optional[Principal]:
    if not token or len(token) > 200:
        return None
    with db.engine().connect() as conn:
        row = conn.execute(
            select(users, sessions.c.csrf_token, sessions.c.expires_at)
            .join(sessions, sessions.c.user_id == users.c.id)
            .where(sessions.c.token_hash == _sha256(token))
        ).first()
    if row is None or not row.active or _expired(row.expires_at):
        return None
    return Principal(row.id, row.email, row.name, row.role, row.must_change_password, "session", row.csrf_token)


def _expired(moment) -> bool:
    now = db.utcnow()
    if moment.tzinfo is None:
        now = now.replace(tzinfo=None)
    return moment < now


# ---------------------------------------------------------------- API keys

def create_api_key(user_id: int, name: str) -> dict:
    """-> {id, name, prefix, key}: `key` is shown once and never stored."""
    name = (name or "").strip()[:100] or "API key"
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    with db.engine().begin() as conn:
        key_id = conn.execute(insert(api_keys).values(
            user_id=user_id, name=name, prefix=prefix, secret_hash=_sha256(secret), created_at=db.utcnow(),
        ).returning(api_keys.c.id)).scalar_one()
    return {"id": key_id, "name": name, "prefix": prefix, "key": f"{KEY_PREFIX}_{prefix}_{secret}"}


def list_api_keys(user_id: Optional[int] = None) -> List[dict]:
    query = select(api_keys.c.id, api_keys.c.user_id, api_keys.c.name, api_keys.c.prefix, api_keys.c.created_at,
                   api_keys.c.last_used_at, api_keys.c.revoked_at, users.c.email, users.c.role) \
        .join(users, users.c.id == api_keys.c.user_id)
    if user_id is not None:
        query = query.where(api_keys.c.user_id == user_id)
    with db.engine().connect() as conn:
        rows = conn.execute(query.order_by(api_keys.c.revoked_at.is_not(None), api_keys.c.created_at.desc()))
        return [dict(r._mapping) for r in rows]


def revoke_api_key(key_id: int, by: Principal) -> bool:
    """Owners revoke their keys; admins any key."""
    with db.engine().begin() as conn:
        query = update(api_keys).where(api_keys.c.id == key_id, api_keys.c.revoked_at.is_(None))
        if not by.is_admin:
            query = query.where(api_keys.c.user_id == by.id)
        return conn.execute(query.values(revoked_at=db.utcnow())).rowcount > 0


def principal_from_api_key(raw: Optional[str]) -> Optional[Principal]:
    parts = (raw or "").strip().split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    _, prefix, secret = parts
    with db.engine().connect() as conn:
        row = conn.execute(
            select(api_keys.c.id.label("key_id"), api_keys.c.name.label("key_name"), api_keys.c.secret_hash,
                   api_keys.c.revoked_at, api_keys.c.last_used_at, users.c.id.label("user_id"),
                   users.c.email, users.c.name.label("user_name"), users.c.role, users.c.active)
            .join(users, users.c.id == api_keys.c.user_id).where(api_keys.c.prefix == prefix)
        ).first()
    if row is None or row.revoked_at is not None or not row.active:
        return None
    if not hmac.compare_digest(row.secret_hash, _sha256(secret)):
        return None
    _touch_key(row.key_id, row.last_used_at)
    return Principal(row.user_id, row.email, row.user_name, row.role, False, "api_key", None, row.key_name)


def _touch_key(key_id: int, last_used):
    """Record use at most once a minute (avoids a write per request)."""
    now = db.utcnow()
    if last_used is not None:
        previous = last_used if last_used.tzinfo else last_used.replace(tzinfo=now.tzinfo)
        if now - previous < timedelta(minutes=1):
            return
    with db.engine().begin() as conn:
        conn.execute(update(api_keys).where(api_keys.c.id == key_id).values(last_used_at=now))


# ---------------------------------------------------------------- request checks

def bearer_token(request) -> Optional[str]:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def resolve(request) -> Optional[Principal]:
    """The principal of a request: API key first, then the session cookie."""
    token = bearer_token(request)
    if token:
        return principal_from_api_key(token)
    return principal_from_session(request.cookies.get(SESSION_COOKIE))


def csrf_ok(principal: Optional[Principal], submitted: Optional[str]) -> bool:
    """API-key requests need no CSRF token (no cookies involved); browser requests do."""
    if principal is None:
        return False
    if principal.via == "api_key":
        return True
    return bool(submitted) and bool(principal.csrf_token) and hmac.compare_digest(submitted, principal.csrf_token)
