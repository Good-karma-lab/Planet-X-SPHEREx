"""`GET /privacy` and `GET /terms`: two plain server pages (not hash routes of the app), so that Google's OAuth consent screen and any
crawler can fetch them at https://www.spherex.space/privacy and /terms."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

REPO = "https://github.com/Good-karma-lab/Planet-X-SPHEREx"
CONTACT = f"{REPO}/issues"
UPDATED = "17 November 2026"

STYLE = """
:root { color-scheme: dark; }
body { margin: 0; background: #05090d; color: #e4ecf0; font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 40px 20px 64px; }
a { color: #5fe3d6; }
h1 { font-size: 30px; margin: 0 0 4px; }
h2 { font-size: 19px; margin: 30px 0 6px; }
p, li { color: #c3d0d6; }
.meta { color: #8fa6b0; font-size: 14px; margin-bottom: 24px; }
nav { display: flex; gap: 18px; font-size: 14px; margin-bottom: 28px; }
"""


def page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · SPHEREx Space</title><meta name="description" content="{title} of SPHEREx Space, www.spherex.space">
<style>{STYLE}</style></head>
<body><main>
<nav><a href="/">SPHEREx Space</a><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav>
{body}
</main></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})


PRIVACY = f"""
<h1>Privacy</h1>
<p class="meta">SPHEREx Space, www.spherex.space · last changed {UPDATED}</p>
<p>You can use SPHEREx Space without an account: look at the sky, open the lab, read Findings. Nothing about you is kept then,
except what your own browser keeps for you (for example marks you put before signing in).</p>

<h2>What we keep when you sign in</h2>
<ul>
<li>your e-mail address, used only to sign you in and to send you the sign-in link and the notices you ask for;</li>
<li>the public name you choose (shown with your finds, on the leaderboard and on your public profile if you turn it on; never your
e-mail);</li>
<li>what you do in the app: your marks, your answers to the tasks, the squares you looked through, your investigations, notes and
submissions, your points and badges, and the history of what you opened;</li>
<li>sign-in links and sessions, stored only as one-way hashes (SHA-256), never as the link or the cookie itself.</li>
</ul>

<h2>Where it is kept</h2>
<p>On our own server in Madrid, Spain, in a database of this service. Sign-in e-mails are sent through Resend (resend.com), which
receives the address and the message to deliver it. If you sign in with Google, Google tells us your e-mail address and name; we
do not receive your password.</p>

<h2>What we do not do</h2>
<ul>
<li>no advertising, no third-party trackers or analytics scripts;</li>
<li>we do not sell or rent your data, and we do not give it to anybody except the mail provider above, to deliver your mail;</li>
<li>counts of shared links are kept without any name, address or cookie.</li>
</ul>

<h2>Your data is yours</h2>
<p>In the app, Cabinet → Account: <b>Download my data</b> gives you what we keep about you in one file, and <b>Delete my account
and my data</b> deletes your account, your e-mail address, your sign-in links and sessions, your marks, tasks, investigations,
history and notifications. Finds you submitted stay in Findings without your name, so that the record of a find is not lost.</p>

<h2>Contact</h2>
<p>Questions about this page or your data: open an issue at <a href="{CONTACT}">{CONTACT}</a>.</p>
"""

TERMS = f"""
<h1>Terms of use</h1>
<p class="meta">SPHEREx Space, www.spherex.space · last changed {UPDATED}</p>

<h2>What this is</h2>
<p>SPHEREx Space is a free citizen-science tool built on the public data of NASA's SPHEREx mission. It is not run by NASA and is
not endorsed by NASA. The code is open source (<a href="{REPO}">{REPO}</a>, Apache-2.0).</p>

<h2>Finds are leads, not discoveries</h2>
<p>A find on this site, by the machine or by a person, is a lead: something that may be worth a closer look. It becomes a candidate
or a confirmed object only after the checks and the review described on the Method page, and a confirmation by other telescopes
is the business of professional astronomy, not of this site.</p>

<h2>Credit</h2>
<ul>
<li>A find is credited to whoever marked it first (a person, or "the machine" for the background search), and the people who
checked it are named with it.</li>
<li>Your public name is shown with your credit; your e-mail never is.</li>
<li>If a find you marked leads to a published result, we will ask the authors to acknowledge the people credited here, but we
cannot promise it.</li>
</ul>

<h2>Acceptable use</h2>
<ul>
<li>Mark what you see honestly; do not mark at random to collect points, and do not use several accounts.</li>
<li>Keep comments and notes polite and about the sky; no spam, no personal data of others.</li>
<li>Do not overload the service with automated requests; the public data are free at their own archives.</li>
</ul>
<p>We may remove marks, comments or points that break these rules, and close accounts that keep doing it.</p>

<h2>No warranty</h2>
<p>The site and its numbers are given as they are, without any warranty. We do our best to keep it running and correct, but it may
be unavailable, slow or wrong at times, and we are not liable for any loss from using it.</p>

<h2>Changes and contact</h2>
<p>We may change these terms; the date above says when they last changed. Questions: <a href="{CONTACT}">{CONTACT}</a>.
See also the <a href="/privacy">privacy page</a>.</p>
"""


def make_legal_router() -> APIRouter:
    router = APIRouter()

    @router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    def privacy() -> HTMLResponse:
        return page("Privacy", PRIVACY)

    @router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    def terms() -> HTMLResponse:
        return page("Terms of use", TERMS)

    return router
