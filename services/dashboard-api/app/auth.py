"""Sign-in, sessions and roles.

The project's central claim is that every price change has a name against it.
That makes authentication load-bearing rather than decorative: `approved_by` in
price_history and `decided_by` in proposals are taken from the signed-in user
and from nowhere else. There is no header, query parameter or request body
field anywhere in this service that can set them.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, Response, status

from . import db
from .config import settings

_hasher = PasswordHasher()

# Roles, most privileged first. A role implies every permission of the ones
# after it, which is why this is an ordered list and not a set.
ROLE_ORDER = ["superadmin", "manager", "staff", "viewer"]

# Roles that may approve prices and change policy. A super admin is a manager
# who also owns the accounts.
MANAGER_ROLES = frozenset({"superadmin", "manager"})

INVITE_TTL_DAYS = 7


def rank(role: str) -> int:
    """0 is the most privileged. An unknown role ranks below everything."""
    try:
        return ROLE_ORDER.index(role)
    except ValueError:
        return len(ROLE_ORDER)


def new_invite_token() -> tuple[str, str]:
    """(token to hand to the invitee, hash to store). Only the hash is kept."""
    token = secrets.token_urlsafe(24)
    return token, hash_invite_token(token)


def hash_invite_token(token: str) -> str:
    # SHA-256, not Argon2: the token is 192 random bits, so there is nothing to
    # brute-force, and it has to be looked up by value.
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(stored_hash: str, plain: str) -> bool:
    try:
        return _hasher.verify(stored_hash, plain)
    except (VerifyMismatchError, InvalidHashError):
        # '!' is the seed's unusable sentinel and lands here as InvalidHashError.
        return False


async def bootstrap_user() -> None:
    """Give the configured bootstrap user a real password hash.

    Used by `make demo` only; `make up` leaves these unset and the first person
    to open the console creates the super admin instead (routers/auth.setup).

    The demo seed ships every user with an unusable '!' hash so that no
    committed file contains a working credential. This turns one of them into
    a super admin someone can actually sign in as, using a password that lives
    only in .env, creating it if the database has no user by that name.
    """
    user = settings.shelfiq_bootstrap_user
    password = settings.shelfiq_bootstrap_password
    if not user or not password:
        return
    email = settings.shelfiq_bootstrap_email
    # The email is kept in step too, so changing it in .env takes effect on the
    # next start rather than only on a fresh database.
    updated = await db.execute(
        """UPDATE users SET password_hash = $2, role = 'superadmin',
                  email = coalesce(nullif($3, ''), email), updated_at = now()
            WHERE username = $1""",
        user,
        hash_password(password),
        email,
    )
    # asyncpg returns the command tag, e.g. "UPDATE 1". Compare the count, not
    # a suffix: "UPDATE 10" ends in "0" too.
    if updated.rsplit(" ", 1)[-1] != "0":
        return
    if not email:
        raise RuntimeError(
            f"SHELFIQ_BOOTSTRAP_USER={user!r} is not a row in the users table, and "
            "SHELFIQ_BOOTSTRAP_EMAIL is empty, so it cannot be created. Set it in .env."
        )
    await db.execute(
        """INSERT INTO users (username, email, full_name, password_hash, role)
           VALUES ($1, $2, $3, $4, 'superadmin')""",
        user,
        email,
        settings.shelfiq_bootstrap_name or user,
        hash_password(password),
    )


async def create_session(username: str, user_agent: str | None) -> tuple[str, datetime]:
    session_id = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    await db.execute(
        """INSERT INTO sessions (session_id, username, expires_at, user_agent)
           VALUES ($1, $2, $3, $4)""",
        session_id,
        username,
        expires,
        (user_agent or "")[:300] or None,
    )
    await db.execute(
        "UPDATE users SET last_login_at = now() WHERE username = $1", username
    )
    return str(session_id), expires


async def destroy_session(session_id: str) -> None:
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return
    await db.execute("DELETE FROM sessions WHERE session_id = $1", sid)


async def purge_expired_sessions() -> None:
    await db.execute("DELETE FROM sessions WHERE expires_at < now()")


def set_session_cookie(response: Response, session_id: str, expires: datetime) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        session_id,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        expires=expires,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


async def start_session(request: Request, response: Response, user: dict) -> dict:
    """Sign `user` (a users row) in: session row, cookie, and the response body
    every sign-in path returns."""
    session_id, expires = await create_session(
        user["username"], request.headers.get("user-agent")
    )
    set_session_cookie(response, session_id, expires)
    return {
        "user": CurrentUser(
            user["username"], user["full_name"], user["role"], user["email"]
        ).as_dict(),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


class CurrentUser:
    def __init__(self, username: str, full_name: str, role: str, email: str):
        self.username = username
        self.full_name = full_name
        self.role = role
        self.email = email

    def initials(self) -> str:
        parts = [p for p in self.full_name.split() if p]
        if not parts:
            return self.username[:2].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def as_dict(self) -> dict:
        return {
            "username": self.username,
            "full_name": self.full_name,
            "role": self.role,
            "email": self.email,
            "initials": self.initials(),
        }


async def _lookup(session_id: str | None) -> CurrentUser | None:
    if not session_id:
        return None
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    row = await db.fetchrow(
        """SELECT u.username, u.full_name, u.role, u.email
             FROM sessions s
             JOIN users u ON u.username = s.username
            WHERE s.session_id = $1
              AND s.expires_at > now()
              AND u.is_active""",
        sid,
    )
    if row is None:
        return None
    return CurrentUser(row["username"], row["full_name"], row["role"], row["email"])


async def current_user(
    shelfiq_session: Annotated[str | None, Cookie()] = None,
) -> CurrentUser:
    user = await _lookup(shelfiq_session)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Sign in to continue.",
        )
    return user


async def optional_user(
    shelfiq_session: Annotated[str | None, Cookie()] = None,
) -> CurrentUser | None:
    return await _lookup(shelfiq_session)


async def user_from_request(request: Request) -> CurrentUser | None:
    """For the WebSocket handler, which has cookies but no dependency wiring."""
    return await _lookup(request.cookies.get(settings.session_cookie_name))


def require_role(*allowed: str):
    """Dependency factory. `require_role("manager")` also admits nobody else —
    roles are listed explicitly rather than ranked implicitly, so widening a
    permission is a visible edit."""

    async def _dep(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action needs the {' or '.join(allowed)} role. "
                f"You are signed in as {user.role}.",
            )
        return user

    return _dep


# Named where they are used rather than spelled out at each call site.
require_superadmin = require_role("superadmin")
require_manager = require_role("superadmin", "manager")
require_editor = require_role("superadmin", "manager", "staff")
