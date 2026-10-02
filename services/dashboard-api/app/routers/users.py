"""Users and roles.

An invite sets an unusable password hash and returns a one-time token, rather
than letting a manager type a colleague's password. Nobody — including a
manager — should ever know another person's credential, because the audit log
is only worth something if `approved_by` identifies one human being.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from .. import auth, bus, db, serialize

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(
    _user: Annotated[auth.CurrentUser, Depends(auth.current_user)],
) -> dict:
    rows = await db.fetch(
        """SELECT u.username, u.email, u.full_name, u.role, u.is_active,
                  u.last_login_at, u.created_at,
                  -- Whether this account can actually sign in. '!' is the
                  -- seed's unusable sentinel, so an un-onboarded colleague
                  -- reads as "invited" rather than looking active.
                  (u.password_hash <> '!') AS can_sign_in,
                  d.decisions
             FROM users u
             LEFT JOIN LATERAL (
                  SELECT count(*) AS decisions FROM proposals
                   WHERE decided_by = u.username
             ) d ON TRUE
            ORDER BY
              CASE u.role WHEN 'manager' THEN 0 WHEN 'staff' THEN 1 ELSE 2 END,
              u.full_name"""
    )
    return {"rows": serialize.rows(rows), "roles": auth.ROLE_ORDER}


class InviteBody(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_.-]{2,32}$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    role: Literal["manager", "staff", "viewer"] = "viewer"


@router.post("", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InviteBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    clash = await db.fetchrow(
        """SELECT username FROM users
            WHERE username = $1 OR lower(email) = lower($2)""",
        body.username,
        body.email,
    )
    if clash:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "That username or email is already in use."
        )

    # '!' cannot be verified against by any password, so the account exists but
    # cannot be signed in to until the invitee sets one.
    await db.execute(
        """INSERT INTO users (username, email, full_name, password_hash, role)
           VALUES ($1, $2, $3, '!', $4)""",
        body.username,
        body.email,
        body.full_name,
        body.role,
    )
    bus.emit("users", changed=[body.username], action="invited")

    # There is no mail transport in this system, so the token is returned to
    # the manager to pass on out of band. It is not stored, so it cannot be
    # read back from here or from the database — losing it means re-inviting.
    return {
        "username": body.username,
        "invite_token": secrets.token_urlsafe(24),
        "note": (
            "This token is shown once and is not stored. Pass it to "
            f"{body.full_name} out of band; they set their own password with it. "
            "No password set here would be theirs alone."
        ),
    }


class RoleBody(BaseModel):
    role: Literal["manager", "staff", "viewer"]


@router.put("/{username}/role")
async def set_role(
    username: str,
    body: RoleBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    if username == user.username and body.role != "manager":
        # Otherwise the last manager can lock the store out of its own
        # pricing controls with one dropdown.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You cannot remove your own manager role. Ask another manager.",
        )
    if body.role != "manager":
        remaining = await db.fetchval(
            """SELECT count(*) FROM users
                WHERE role = 'manager' AND is_active AND username <> $1""",
            username,
        )
        if not remaining:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This is the only manager. Promote someone else first.",
            )

    updated = await db.execute(
        "UPDATE users SET role = $2, updated_at = now() WHERE username = $1",
        username,
        body.role,
    )
    if updated.rsplit(" ", 1)[-1] == "0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")

    await db.execute(
        """INSERT INTO agent_audit_log (event_type, actor, detail)
           VALUES ('guardrail_set', $1, $2)""",
        user.username,
        {"scope": "role", "username": username, "role": body.role},
    )
    bus.emit("users", changed=[username], action="role")
    return {"username": username, "role": body.role}


class ActiveBody(BaseModel):
    is_active: bool


@router.put("/{username}/active")
async def set_active(
    username: str,
    body: ActiveBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    if username == user.username and not body.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot deactivate your own account."
        )
    updated = await db.execute(
        "UPDATE users SET is_active = $2, updated_at = now() WHERE username = $1",
        username,
        body.is_active,
    )
    if updated.rsplit(" ", 1)[-1] == "0":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")

    if not body.is_active:
        # Server-side sessions exist precisely so this takes effect now rather
        # than whenever their token would have expired.
        await db.execute("DELETE FROM sessions WHERE username = $1", username)

    bus.emit("users", changed=[username], action="active")
    return {"username": username, "is_active": body.is_active}
