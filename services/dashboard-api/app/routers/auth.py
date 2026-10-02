"""Sign in, sign out, who am I."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from .. import auth, db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignInBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


@router.post("/signin")
async def signin(body: SignInBody, request: Request, response: Response) -> dict:
    user = await db.fetchrow(
        """SELECT username, full_name, role, email, password_hash, is_active
             FROM users WHERE lower(email) = lower($1)""",
        body.email,
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
            status.HTTP_401_UNAUTHORIZED, "That email and password do not match."
        )

    session_id, expires = await auth.create_session(
        user["username"], request.headers.get("user-agent")
    )
    auth.set_session_cookie(response, session_id, expires)
    return {
        "user": auth.CurrentUser(
            user["username"], user["full_name"], user["role"], user["email"]
        ).as_dict(),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }


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
    # an error in the console.
    return {"user": user.as_dict() if user else None}
