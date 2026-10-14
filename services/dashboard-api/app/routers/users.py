"""Users and roles.

An invite sets an unusable password hash and returns a one-time link, rather
than letting a manager type a colleague's password. Nobody — including a
manager — should ever know another person's credential, because the audit log
is only worth something if `approved_by` identifies one human being.

Rank rule: you manage only accounts ranked below you, and grant only roles
below yours. A super admin manages everyone, other super admins included, so
a manager can run staff and viewers but cannot touch a manager or above.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from .. import auth, bus, db, serialize

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(
    _user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Manager-only.

    This is a directory of colleagues' email addresses, roles and last sign-in
    times. It was readable by any signed-in account, including a Read only one
    whose whole purpose is that it can do nothing — and no other screen needs
    it. The Users screen renders the permission matrix alone on a 403.
    """
    rows = await db.fetch(
        """SELECT u.username, u.email, u.full_name, u.role, u.is_active,
                  u.last_login_at, u.created_at,
                  -- Whether this account can actually sign in. '!' is the
                  -- seed's unusable sentinel, so an un-onboarded colleague
                  -- reads as "invited" rather than looking active.
                  (u.password_hash <> '!') AS can_sign_in,
                  d.decisions, pol.policy_rows
             FROM users u
             LEFT JOIN LATERAL (
                  SELECT count(*) AS decisions FROM proposals
                   WHERE decided_by = u.username
             ) d ON TRUE
             LEFT JOIN LATERAL (
                  -- category_rules.set_by and change_limits.set_by are foreign
                  -- keys with no ON DELETE, so a user who has saved a rule
                  -- cannot be deleted. The screen needs to know before it
                  -- offers the verb.
                  SELECT (SELECT count(*) FROM category_rules WHERE set_by = u.username)
                       + (SELECT count(*) FROM change_limits  WHERE set_by = u.username)
                         AS policy_rows
             ) pol ON TRUE
            ORDER BY
              CASE u.role WHEN 'superadmin' THEN 0 WHEN 'manager' THEN 1
                          WHEN 'staff' THEN 2 ELSE 3 END,
              u.full_name"""
    )
    return {"rows": serialize.rows(rows), "roles": auth.ROLE_ORDER}


Role = Literal["superadmin", "manager", "staff", "viewer"]


def _assert_can_manage(actor: auth.CurrentUser, *roles: str) -> None:
    """`roles` are the target's current role and, for a change, the new one."""
    if actor.role == "superadmin":
        return
    if any(auth.rank(r) <= auth.rank(actor.role) for r in roles):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Only a super admin can manage manager and super admin accounts.",
        )


async def _target_role(username: str) -> str:
    role = await db.fetchval("SELECT role FROM users WHERE username = $1", username)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")
    return role


async def _issue_invite(username: str, actor: auth.CurrentUser) -> dict:
    """Replace any outstanding invite for `username` with a new one."""
    token, token_hash = auth.new_invite_token()
    expires = datetime.now(timezone.utc) + timedelta(days=auth.INVITE_TTL_DAYS)
    async with db.pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM user_invites WHERE username = $1", username)
            await conn.execute(
                """INSERT INTO user_invites (token_hash, username, created_by, expires_at)
                   VALUES ($1, $2, $3, $4)""",
                token_hash,
                username,
                actor.username,
                expires,
            )
    return {
        "username": username,
        "invite_token": token,
        "invite_path": f"/join?token={token}",
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


class InviteBody(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_.-]{2,32}$")
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    role: Role = "viewer"


@router.post("", status_code=status.HTTP_201_CREATED)
async def invite(
    body: InviteBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    _assert_can_manage(user, body.role)
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

    # There is no mail transport in this system, so the link is returned to
    # the inviter to pass on out of band. Only its hash is stored, so it
    # cannot be read back later — losing it means sending a new invite.
    return await _issue_invite(body.username, user) | {
        "note": (
            "This link is shown once. Pass it to "
            f"{body.full_name} out of band; they set their own password with it. "
            f"It expires in {auth.INVITE_TTL_DAYS} days."
        ),
    }


@router.post("/{username}/invite")
async def reinvite(
    username: str,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """A fresh link for someone who has not joined yet. Any older link stops
    working. Also how a seeded demo colleague gets a password."""
    row = await db.fetchrow(
        """SELECT role, full_name, is_active, (password_hash <> '!') AS can_sign_in
             FROM users WHERE username = $1""",
        username,
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")
    _assert_can_manage(user, row["role"])
    if row["can_sign_in"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"{row['full_name']} has already joined."
        )
    if not row["is_active"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Restore {row['full_name']}'s access first."
        )
    bus.emit("users", changed=[username], action="invited")
    return await _issue_invite(username, user)


async def _assert_not_last_superadmin(username: str, action: str) -> None:
    """Refuse to strip the store of its only super admin.

    Without one, nobody could ever again grant or remove the manager role, so
    the account structure would be frozen for good.
    """
    row = await db.fetchrow(
        "SELECT role, is_active FROM users WHERE username = $1", username
    )
    if row is None or row["role"] != "superadmin" or not row["is_active"]:
        return
    remaining = await db.fetchval(
        """SELECT count(*) FROM users
            WHERE role = 'superadmin' AND is_active AND username <> $1""",
        username,
    )
    if not remaining:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This is the only active super admin. Make someone else a super admin before you {action}.",
        )


class RoleBody(BaseModel):
    role: Role


@router.put("/{username}/role")
async def set_role(
    username: str,
    body: RoleBody,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    if username == user.username and auth.rank(body.role) > auth.rank(user.role):
        # Otherwise one dropdown can lock you out of the controls you are
        # using, and possibly the store with you.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "You cannot lower your own role. Ask a super admin.",
        )
    _assert_can_manage(user, await _target_role(username), body.role)
    if body.role != "superadmin":
        await _assert_not_last_superadmin(username, "change this role")

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
    _assert_can_manage(user, await _target_role(username))
    if not body.is_active:
        await _assert_not_last_superadmin(username, "remove their access")
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


@router.delete("/{username}")
async def delete_user(
    username: str,
    user: Annotated[auth.CurrentUser, Depends(auth.require_manager)],
) -> dict:
    """Revoke an invitation that was never used.

    **This is not how you remove a colleague who has worked here.** Their name
    is on every price they approved, in an append-only table with no foreign key
    to delete through, and `category_rules.set_by` *is* a foreign key with no
    `ON DELETE` — so a hard delete would either fail or leave an orphan name in
    the audit trail. Removing access for anyone who has signed in is
    `PUT /{username}/active {is_active: false}`, and the UI says which verb it
    is using.

    All three conditions are rechecked here rather than trusted from the client.
    """
    if username == user.username:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "You cannot delete your own account."
        )

    async with db.pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT u.full_name, u.role, (u.password_hash <> '!') AS can_sign_in,
                          u.last_login_at,
                          (SELECT count(*) FROM proposals WHERE decided_by = u.username)
                            AS decisions,
                          (SELECT count(*) FROM category_rules WHERE set_by = u.username)
                        + (SELECT count(*) FROM change_limits  WHERE set_by = u.username)
                            AS policy_rows
                     FROM users u WHERE u.username = $1""",
                username,
            )
            if row is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user.")
            _assert_can_manage(user, row["role"])

            if row["can_sign_in"] or row["last_login_at"] is not None:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{row['full_name']} has signed in. Remove their access "
                    "instead — their name stays on the decisions they made.",
                )
            if row["decisions"]:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{row['full_name']} has decided on {row['decisions']} "
                    "price suggestions. Remove their access instead.",
                )
            if row["policy_rows"]:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"{row['full_name']} has set pricing rules that still "
                    "reference them. Remove their access instead.",
                )

            await conn.execute("DELETE FROM sessions WHERE username = $1", username)
            await conn.execute("DELETE FROM users WHERE username = $1", username)

    bus.emit("users", changed=[username], action="deleted")
    return {"deleted": username, "by": user.username}
