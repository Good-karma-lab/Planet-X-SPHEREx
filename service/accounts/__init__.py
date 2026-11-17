"""Accounts of the product flow: sign-in by e-mail link and Google, the session cookie, and the user's data.

One call wires everything into the app: `register_accounts(app, st)` (service/app.py belongs to the integrator). It also sets
`st.current_user(request) -> {id, name, email} | None`, which other product modules use to know who is asking.

Hooks that other streams fill (each may be absent; the routes that need them then answer 503):
  st.checks_status(job) -> the body of GET /api/checks/{job} ({state, checks, can_submit, ...}) or None for an unknown job
and one hook this module sets for the checks stream: st.on_checks_done(body), called when a job reaches its final state
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request

from service.accounts.db import AccountsDB
from service.accounts.mail import LogSender, MailSender, sender_from_env
from service.ratelimit import Buckets, client_id
from service.storage import product_db_path

COOKIE = "sx_session"
OAUTH_COOKIE = "sx_oauth"
SESSION_S = 30 * 86400.0
LINK_MINUTES = 15
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
#: for Google sign-in through the web dev server set SKY_PUBLIC_URL=http://localhost:<web port> (the redirect address must be whole)


class Accounts:
    def __init__(self, st: Any) -> None:
        self.st = st
        self.db = AccountsDB(product_db_path(st.cfg.store_dir))
        self.mail: MailSender = sender_from_env()  # Resend with SKY_RESEND_API_KEY, else the API log
        self.http_transport: Any = None  # tests put a fake Google here
        # sign-in: 5 links per 10 minutes per client and 3 per address; 20 link openings per 10 minutes per client
        self.links_by_client = Buckets(capacity=5, per_second=5 / 600)
        self.links_by_address = Buckets(capacity=3, per_second=3 / 600)
        self.openings = Buckets(capacity=20, per_second=20 / 600)
        self.writes = Buckets(capacity=240, per_second=4.0)  # a user's own writes (marks, notes, history)

    # --------------------------------------------------------------------------------------------------------------- environment

    @property
    def dev(self) -> bool:
        """Dev mode shows the sign-in link on the page. Never set it on a public server."""
        return os.environ.get("SKY_AUTH_DEV", "0") == "1"

    @property
    def email(self) -> bool:
        """E-mail sign-in is open: a sender that really sends mail is set, or dev mode shows the link on the page. With neither
        the site says "Sign-in opens soon" and takes no address."""
        return self.dev or not isinstance(self.mail, LogSender)

    @property
    def google(self) -> tuple[str, str] | None:
        cid, secret = os.environ.get("SKY_GOOGLE_CLIENT_ID"), os.environ.get("SKY_GOOGLE_CLIENT_SECRET")
        return (cid, secret) if cid and secret else None

    def who(self, request: Request) -> str:
        return client_id(request, getattr(self.st, "salt", ""))

    # ------------------------------------------------------------------------------------------------------------------ sessions

    def current_user(self, request: Request) -> dict[str, Any] | None:
        token = request.cookies.get(COOKIE)
        if not token or len(token) > 128:
            return None
        return self.db.session_user(token)

    def need_user(self, request: Request) -> dict[str, Any]:
        """The signed-in user, or 401. A request that changes something must also come from our own page."""
        u = self.current_user(request)
        if u is None:
            raise HTTPException(401, "sign in first")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            check_origin(request)
            wait = self.writes.take(u["id"])
            if wait > 0:
                raise HTTPException(429, "too many changes at once", headers={"Retry-After": str(int(wait) + 1)})
        return u


def is_local(request: Request) -> bool:
    return (request.url.hostname or "") in LOCAL_HOSTS


def public_base(request: Request) -> str:
    """The address the browser sees. On a public server it is `SKY_PUBLIC_URL`, never the request's Host header (a forged Host would
    put somebody else's address into a sign-in link); the Host is trusted only on localhost."""
    env = os.environ.get("SKY_PUBLIC_URL", "").rstrip("/")
    if env:
        return env
    if is_local(request):
        # the web dev server proxies /api and puts the API's own address into Host: the page's address is in Origin
        origin = request.headers.get("origin")
        if origin and (urlparse(origin).hostname or "") in LOCAL_HOSTS:
            return origin.rstrip("/")
        return f"{request.url.scheme}://{request.headers.get('host') or request.url.netloc}"
    raise HTTPException(503, "sign-in is not set up on this server (SKY_PUBLIC_URL)")


def check_origin(request: Request) -> None:
    """A browser names the page that sends a changing request; another site's page is refused (the cookie is SameSite=Lax as well)."""
    origin = request.headers.get("origin")
    if not origin:
        return
    allowed = {request.headers.get("host"), urlparse(os.environ.get("SKY_PUBLIC_URL", "")).netloc or None}
    if is_local(request) and (urlparse(origin).hostname or "") in LOCAL_HOSTS:
        return  # local work: the web dev server on another port
    if urlparse(origin).netloc not in allowed:
        raise HTTPException(403, "this request comes from another site")


def get(st: Any) -> Accounts:
    """The accounts of this service (opened at the first use)."""
    acc: Accounts | None = getattr(st, "accounts", None)
    if acc is None:
        acc = Accounts(st)
        st.accounts = acc
        st.current_user = acc.current_user
    return acc


def register_accounts(app: FastAPI, st: Any) -> None:
    """Sign-in, the user's data, tasks and submit: every route of the accounts stream."""
    from service.routes_auth import make_auth_router
    from service.routes_me import make_me_router
    from service.routes_share import make_share_router
    from service.routes_tasks import make_tasks_router

    st.current_user = lambda request: get(st).current_user(request)
    # the checks stream calls this when a job reaches its final state: declared submits finish without any page open
    from service.accounts import submit

    st.on_checks_done = lambda body: submit.settle_job(st, get(st).db, str(body.get("job") or ""))
    app.include_router(make_auth_router(st))
    app.include_router(make_me_router(st))
    app.include_router(make_tasks_router(st))
    app.include_router(make_share_router(st))  # public profiles, stories, share links and their pictures


__all__ = ["COOKIE", "Accounts", "AccountsDB", "MailSender", "get", "register_accounts"]
