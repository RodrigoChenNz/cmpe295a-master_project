"""Sign in, sign out, who am I — plus the two ways an account gets a password:
first-run setup (the store's first super admin) and accepting an invite."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from .. import auth, bus, db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignInBody(BaseModel):
    # An email or a username. A username can never contain "@" (users table
    # CHECK), so which one was meant is never ambiguous.
    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)
    # Stay signed in across browser restarts, for session_remember_days.
    remember: bool = False


@router.post("/signin")
async def signin(body: SignInBody, request: Request, response: Response) -> dict:
    login = body.login.strip()
    column = "lower(email)" if "@" in login else "username"
    user = await db.fetchrow(
        f"""SELECT username, full_name, role, email, password_hash, is_active
              FROM users WHERE {column} = lower($1)""",
        login,
    )

    # One failure message and one timing profile for every cause: no account,
    # wrong password, deactivated account. Telling them apart would turn the
    # sign-in form into a way to enumerate who works here.
    ok = (
        user is not None
        and user["is_active"]
        and auth.verify_password(user["password_hash"], body.password)
    )
    if not ok:
        await asyncio.sleep(0.4)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Those sign-in details do not match."
        )

    return await auth.start_session(request, response, user, remember=body.remember)


@router.post("/signout")
async def signout(
    response: Response,
    shelfiq_session: Annotated[str | None, Cookie()] = None,
) -> dict:
    if shelfiq_session:
        await auth.destroy_session(shelfiq_session)
    auth.clear_session_cookie(response)
    return {"signed_out": True}


@router.get("/me")
async def me(
    user: Annotated[auth.CurrentUser | None, Depends(auth.optional_user)],
) -> dict:
    # 200 with user: null rather than 401, so the app can decide between the
    # sign-in screen and the console without treating a normal cold start as
    # an error in the console. setup_required picks the first-run screen
    # instead of sign-in on a store with no accounts at all, and
    # onboarding_required the setup wizard instead of the console on a store
    # nobody has configured yet.
    #
    # The store's name and number go out signed in or not: the sign-in screen
    # shows them, and they are on the sign above the door anyway.
    store = await db.fetchrow(
        "SELECT store_name, store_code, onboarded_at FROM store_settings LIMIT 1"
    )
    brand = (
        {"name": store["store_name"], "code": store["store_code"]}
        if store and store["store_name"]
        else None
    )
    if user:
        return {
            "user": user.as_dict(),
            "setup_required": False,
            "onboarding_required": not (store and store["onboarded_at"]),
            "store": brand,
        }
    has_users = await db.fetchval("SELECT EXISTS (SELECT 1 FROM users)")
    return {
        "user": None,
        "setup_required": not has_users,
        "onboarding_required": False,
        "store": brand,
    }


# Shared by setup and invite acceptance. Sign-in deliberately has no length
# rule: it must accept whatever was set, and say nothing about why it failed.
Password = Annotated[str, Field(min_length=10, max_length=256)]


class SetupBody(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_.-]{2,32}$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    password: Password


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(body: SetupBody, request: Request, response: Response) -> dict:
    """Create the store's first account, as super admin, and sign it in.

    Open to anyone, which is only safe because it works exactly once: the
    table lock makes two simultaneous first visitors queue, and the second
    finds a user and is refused.
    """
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("LOCK TABLE users IN EXCLUSIVE MODE")
            if await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users)"):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This store is already set up. Sign in, or ask a super admin for an invite.",
                )
            user = await conn.fetchrow(
                """INSERT INTO users (username, email, full_name, password_hash, role)
                   VALUES ($1, $2, $3, $4, 'superadmin')
                RETURNING username, full_name, role, email""",
                body.username,
                body.email,
                body.full_name,
                auth.hash_password(body.password),
            )
            await conn.execute(
                """INSERT INTO agent_audit_log (event_type, actor, detail)
                   VALUES ('guardrail_set', $1, $2)""",
                body.username,
                {"scope": "role", "username": body.username, "role": "superadmin",
                 "via": "first-run setup"},
            )
    bus.emit("users", changed=[body.username], action="setup")
    return await auth.start_session(request, response, dict(user))


async def _invite(token: str) -> dict:
    row = await db.fetchrow(
        """SELECT u.username, u.email, u.full_name, u.role, u.is_active
             FROM user_invites i
             JOIN users u ON u.username = i.username
            WHERE i.token_hash = $1 AND i.expires_at > now()""",
        auth.hash_invite_token(token),
    )
    if row is None or not row["is_active"]:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This invite link is invalid or has expired. Ask for a new one.",
        )
    return row


@router.get("/invite/{token}")
async def get_invite(token: str) -> dict:
    """Who an invite is for, so the join screen can greet them by name."""
    row = await _invite(token)
    return {k: row[k] for k in ("username", "email", "full_name", "role")}


class AcceptBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: Password


@router.post("/invite/accept")
async def accept_invite(body: AcceptBody, request: Request, response: Response) -> dict:
    row = await _invite(body.token)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE users SET password_hash = $2, updated_at = now() WHERE username = $1",
                row["username"],
                auth.hash_password(body.password),
            )
            # Every invite for them, not just this one: any other link that
            # was passed around must stop working now that they have joined.
            await conn.execute(
                "DELETE FROM user_invites WHERE username = $1", row["username"]
            )
    bus.emit("users", changed=[row["username"]], action="joined")
    return await auth.start_session(request, response, row)
