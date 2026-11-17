"""Sharing (product spec §5c; service/accounts/share.py).

GET   /api/u/{slug}                       the public profile {name, points, level, badges, findings} (404 while its owner keeps it private)
GET   /api/me/profile                     {slug, public};  PATCH /api/me {public_profile} turns it on or off
GET   /api/stories/{finding_id}           the story of a submitted finding (public ones; its owner also sees the draft)
GET   /api/me/stories/{finding_id}        the owner's story, a draft made from the investigation at the first ask
PUT   /api/me/stories/{finding_id} {title?, intro?, notes?, public?}   edit; `public: true` publishes
GET   /api/discoveries/latest             {discovery: {finding_id, title, finder, summary, url, page, image} | null}  (for the landing)
POST  /api/share {kind, ref, channel}     counts one share and answers {url, title, text}: the page then opens the network's own
                                          share window. kind: profile | achievement | story | discovery; ref: the slug,
                                          `<slug>.<achievement id>`, or the finding id
GET   /api/share/counts                   {counts:[{kind, ref, channel, shares, visits, previews}]}  (no personal data)
GET   /s/{kind}/{ref}                     the shared link: an HTML page with the Open Graph tags that sends people on to the app
GET   /s/{kind}/{ref}/image.png           its picture, 1200 × 630
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from service import accounts
from service.accounts import public_base, share


class StoryIn(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=140)
    intro: str | None = Field(None, max_length=6000)
    notes: str | None = Field(None, max_length=12000)
    public: bool | None = None


class ShareIn(BaseModel):
    kind: str = Field(pattern="^(profile|achievement|story|discovery)$")
    ref: str = Field(min_length=1, max_length=200)
    channel: str = Field(pattern="^(native|x|linkedin|facebook|reddit|telegram|whatsapp|copy)$")


def make_share_router(st: Any) -> APIRouter:
    router = APIRouter()

    def sh() -> share.Share:
        return share.get(st, accounts.get(st).db)

    def viewer(request: Request) -> str | None:
        u = accounts.get(st).current_user(request)
        return u["id"] if u else None

    @router.get("/api/u/{slug}")
    def profile(slug: str, request: Request) -> Any:
        p = sh().profile(slug, viewer(request))
        if p is None:
            raise HTTPException(404, "no such public profile")
        return p

    @router.get("/api/me/profile")
    def my_profile(request: Request) -> Any:
        u = accounts.get(st).need_user(request)
        s = sh()
        return {"slug": s.slug(u["id"]), "public": s.is_public(u["id"])}

    @router.get("/api/discoveries/latest")
    def latest() -> Any:
        return {"discovery": sh().latest_discovery()}

    @router.get("/api/me/stories/{finding_id:path}")
    def my_story(finding_id: str, request: Request) -> Any:
        u = accounts.get(st).need_user(request)
        s = sh().story(finding_id, u["id"], make=True)
        if s is None or not s["mine"]:
            raise HTTPException(404, "no such finding of yours")
        return s

    @router.put("/api/me/stories/{finding_id:path}")
    def edit_story(finding_id: str, body: StoryIn, request: Request) -> Any:
        u = accounts.get(st).need_user(request)
        cur = sh().story(finding_id, u["id"], make=True)
        if cur is None or not cur["mine"]:
            raise HTTPException(404, "no such finding of yours")
        return sh().edit_story(finding_id, u["id"], body.model_dump(exclude_unset=True))

    @router.get("/api/stories/{finding_id:path}")
    def story(finding_id: str, request: Request) -> Any:
        s = sh().story(finding_id, viewer(request))
        if s is None:
            raise HTTPException(404, "no such public story")
        return s

    @router.post("/api/share")
    def do_share(body: ShareIn, request: Request) -> Any:
        """One click of a share button. Sharing a profile or an achievement makes the profile public (the user chose to show it)."""
        s = sh()
        me = viewer(request)
        if body.kind in ("profile", "achievement"):
            u = accounts.get(st).need_user(request)
            if s.slug(u["id"]) != body.ref.partition(".")[0]:
                raise HTTPException(403, "only your own profile and achievements can be shared from here")
            s.set_public(u["id"], True)
        t = s.target(body.kind, body.ref, me)
        if t is None or (body.kind in ("story", "discovery") and not (s.story(body.ref) or {}).get("public")):
            raise HTTPException(409, "this is not public yet: publish the story first")
        s.count(body.kind, body.ref, body.channel, "shares")
        url = f"{public_base(request)}/s/{body.kind}/{quote(body.ref, safe='')}?c={body.channel}"
        return {"url": url, "title": t["title"], "text": t["text"]}

    @router.get("/api/share/counts")
    def counts() -> Any:
        return {"counts": sh().counts()}

    @router.get("/s/{kind}/{ref:path}/image.png")
    def image(kind: str, ref: str) -> Response:
        png = sh().image(kind, ref) if kind in share.KINDS else None
        if png is None:
            raise HTTPException(404, "nothing public here")
        return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @router.get("/s/{kind}/{ref:path}", response_class=HTMLResponse)
    def shared(kind: str, ref: str, request: Request, c: str = "link") -> Response:
        """Social crawlers read the tags and stop; people are sent on to the app. A visit is counted per link and channel only."""
        s = sh()
        t = s.target(kind, ref) if kind in share.KINDS else None
        if t is None:  # private or gone: the app itself, with nothing said about why
            return HTMLResponse(
                '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=/"><title>SPHEREx Space</title>', 404
            )
        s.count(kind, ref, c, "previews" if share.CRAWLER.search(request.headers.get("user-agent", "")) else "visits")
        base = public_base(request)
        here = f"{base}/s/{kind}/{quote(ref, safe='')}"
        e = html.escape
        page = f"/{t['page']}"
        body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{e(t["title"])}</title>
<meta name="description" content="{e(t["text"])}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="{e(share.SITE)}">
<meta property="og:title" content="{e(t["title"])}">
<meta property="og:description" content="{e(t["text"])}">
<meta property="og:url" content="{e(here)}">
<meta property="og:image" content="{e(here)}/image.png">
<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(t["title"])}">
<meta name="twitter:description" content="{e(t["text"])}">
<meta name="twitter:image" content="{e(here)}/image.png">
<meta http-equiv="refresh" content="0; url={e(page)}">
</head><body style="background:#040b10;color:#d6e7ed;font-family:system-ui,sans-serif;padding:24px">
<p><a style="color:#5fe3d6" href="{e(page)}">{e(t["title"])}</a></p><p>{e(t["text"])}</p>
<script>location.replace({page!r})</script>
</body></html>"""
        return HTMLResponse(body, headers={"Cache-Control": "no-cache"})

    return router
