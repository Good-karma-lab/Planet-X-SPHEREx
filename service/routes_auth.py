"""Sign-in without passwords: a single-use e-mail link, and Google when `SKY_GOOGLE_CLIENT_ID` / `SKY_GOOGLE_CLIENT_SECRET` are set.

  GET  /api/me                      {user:{id, name, email}|null, auth:{email, google, dev}}   (email false and google false:
                                    "Sign-in opens soon"; POST /api/auth/email then answers 503)
  POST /api/auth/email {email, next?}   → {sent:true, minutes, dev_link?}   (dev_link only with SKY_AUTH_DEV=1)
  GET  /api/auth/callback?token=    sets the session cookie, goes to the cabinet (or to `next`)
  GET  /api/auth/google             goes to Google; GET /api/auth/google/callback comes back
  POST /api/auth/logout             ends the session

The session cookie is HttpOnly, SameSite=Lax, and Secure everywhere except localhost.
"""

from __future__ import annotations

import os
import re
import secrets
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from service import accounts
from service.accounts import COOKIE, LINK_MINUTES, OAUTH_COOKIE, SESSION_S, check_origin, is_local, public_base
from service.accounts.mail import MailFailedError

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NEXT = re.compile(r"^#/[A-Za-z0-9_\-/?=&.%+:,~]*$")  # only a place inside this app
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"


def google_public() -> bool:
    """The "Continue with Google" button is shown only once the Google app is published (SKY_GOOGLE_PUBLIC=1): until then Google
    lets only its test users in. The routes themselves work whenever the keys are set (for those test users)."""
    return os.environ.get("SKY_GOOGLE_PUBLIC", "0") == "1"


GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"


class EmailIn(BaseModel):
    email: str = Field(max_length=254)
    next: str | None = Field(None, max_length=600)


def make_auth_router(st: Any) -> APIRouter:
    router = APIRouter()

    def limited(wait: float) -> None:
        if wait > 0:
            raise HTTPException(429, "too many sign-in attempts; try again later", headers={"Retry-After": str(int(wait) + 1)})

    def signed_in(request: Request, user: dict[str, Any], nxt: str | None) -> Response:
        acc = accounts.get(st)
        token = acc.db.new_session(user["id"], SESSION_S)
        # a relative address: the browser stays on the site it opened the link on (also behind the web dev server's proxy)
        resp = RedirectResponse(f"/{nxt if nxt and NEXT.match(nxt) else '#/me'}", status_code=303)
        resp.set_cookie(COOKIE, token, max_age=int(SESSION_S), httponly=True, samesite="lax", secure=not is_local(request), path="/")
        return resp

    def failed(request: Request, why: str, nxt: str | None = None) -> Response:
        """Back where the person came from with `signin=<why>` (the lab, not the cabinet), else the cabinet."""
        if nxt and NEXT.match(nxt):
            return RedirectResponse(f"/{nxt}{'&' if '?' in nxt else '?'}signin={why}", status_code=303)
        return RedirectResponse(f"/#/me?signin={why}", status_code=303)

    @router.get("/api/me")
    def me(request: Request) -> Any:
        acc = accounts.get(st)
        u = acc.current_user(request)
        if u is not None:
            from service.accounts.review import Review

            u = {**u, "reviewer": Review(acc.db).is_reviewer(u["id"])}  # the cabinet shows the review page's link
        return {
            "user": u,
            "auth": {"email": acc.email, "google": acc.google is not None and google_public(), "dev": acc.dev},
        }

    @router.post("/api/auth/email")
    def email_link(body: EmailIn, request: Request) -> Any:
        acc = accounts.get(st)
        check_origin(request)
        if not acc.email:  # no mail sender is set up: no address is taken, no token is made
            raise HTTPException(503, "Sign-in opens soon")
        email = body.email.strip().lower()
        if not EMAIL.match(email):
            raise HTTPException(422, "this does not look like an e-mail address")
        base = public_base(request)
        limited(acc.links_by_client.take(acc.who(request)))
        limited(acc.links_by_address.take(email))
        nxt = body.next if body.next and NEXT.match(body.next) else None
        token = acc.db.new_login_token(email, LINK_MINUTES * 60.0, nxt)
        link = f"{base}/api/auth/callback?token={token}"
        try:
            acc.mail.send_link(email, link, LINK_MINUTES)
        except MailFailedError:
            raise HTTPException(502, "The mail could not be sent. Try again in a minute.") from None
        out: dict[str, Any] = {"sent": True, "minutes": LINK_MINUTES}
        if acc.dev:
            out["dev_link"] = link
        return out

    @router.get("/api/auth/callback")
    def callback(token: str, request: Request) -> Response:
        acc = accounts.get(st)
        limited(acc.openings.take(acc.who(request)))
        used = acc.db.use_login_token(token) if len(token) <= 128 else None
        if used is None:
            return failed(request, "expired")
        email, nxt = used
        return signed_in(request, acc.db.user_for_email(email), nxt)

    @router.get("/api/auth/google")
    def google(request: Request, next: str | None = None) -> Response:
        acc = accounts.get(st)
        if acc.google is None:
            raise HTTPException(404, "Google sign-in is not set up")
        base = public_base(request)
        host = urlparse(base).hostname
        if not is_local(request) and host and request.url.hostname and request.url.hostname != host:
            # Google comes back to the public address: the state cookie (and `next` in it) must be set there; set on another name of
            # the site (spherex.space for www.spherex.space) it was not sent back, the sign-in failed and landed in the cabinet
            return RedirectResponse(f"{base}/api/auth/google{'?' + urlencode({'next': next}) if next and NEXT.match(next) else ''}", 303)
        limited(acc.openings.take(acc.who(request)))
        state = secrets.token_urlsafe(24)
        query = urlencode(
            {
                "client_id": acc.google[0],
                "redirect_uri": f"{base}/api/auth/google/callback",
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "prompt": "select_account",
            }
        )
        resp = RedirectResponse(f"{GOOGLE_AUTH}?{query}", status_code=303)
        keep = f"{state}|{next}" if next and NEXT.match(next) else state
        resp.set_cookie(OAUTH_COOKIE, keep, max_age=600, httponly=True, samesite="lax", secure=not is_local(request), path="/api/auth")
        return resp

    @router.get("/api/auth/google/callback")
    async def google_callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
        acc = accounts.get(st)
        if acc.google is None:
            raise HTTPException(404, "Google sign-in is not set up")
        limited(acc.openings.take(acc.who(request)))
        kept, _, nxt = (request.cookies.get(OAUTH_COOKIE) or "").partition("|")
        if not code or not state or not kept or not secrets.compare_digest(kept, state):
            return failed(request, "google", nxt or None)
        try:
            async with httpx.AsyncClient(transport=acc.http_transport, timeout=15.0) as http:
                tok = await http.post(
                    GOOGLE_TOKEN,
                    data={
                        "code": code,
                        "client_id": acc.google[0],
                        "client_secret": acc.google[1],
                        "redirect_uri": f"{public_base(request)}/api/auth/google/callback",
                        "grant_type": "authorization_code",
                    },
                )
                tok.raise_for_status()
                info = await http.get(GOOGLE_USERINFO, headers={"Authorization": f"Bearer {tok.json()['access_token']}"})
                info.raise_for_status()
                who = info.json()
        except (httpx.HTTPError, KeyError, ValueError):
            return failed(request, "google", nxt or None)
        email = str(who.get("email") or "").strip().lower()
        if not who.get("email_verified") or not EMAIL.match(email):
            return failed(request, "google", nxt or None)
        user = acc.db.user_for_email(email, name=str(who.get("name") or "") or None, google_sub=str(who.get("sub") or "") or None)
        resp = signed_in(request, user, nxt or None)
        resp.delete_cookie(OAUTH_COOKIE, path="/api/auth")
        return resp

    @router.post("/api/auth/logout")
    def logout(request: Request, response: Response) -> Any:
        acc = accounts.get(st)
        check_origin(request)
        token = request.cookies.get(COOKIE)
        if token:
            acc.db.end_session(token)
        response.delete_cookie(COOKIE, path="/")
        return {"user": None}

    return router
