"""Sending the sign-in link. The sender is a small interface:

  LogSender      the default: no mail leaves the machine, the link is written to the API log. With it (and no dev mode) the site
                 says "Sign-in opens soon" instead of taking addresses it cannot write to.
  ResendSender   real mail through Resend (https://resend.com), used when `SKY_RESEND_API_KEY` is set; the sender's address is
                 `SKY_MAIL_FROM` (default "SPHEREx Space <signin@spherex.space>": the domain must be verified at Resend).

Another sender is plugged in with `accounts.get(st).mail = MySender()`."""

from __future__ import annotations

import html
import logging
import os
from typing import Any, Protocol

import httpx

log = logging.getLogger("uvicorn.error")


class MailSender(Protocol):
    def send_link(self, email: str, link: str, minutes: int) -> None:
        """Deliver the single-use sign-in link to this address."""
        ...


class LogSender:
    """No mail leaves the machine: the link is written to the API log."""

    def send_link(self, email: str, link: str, minutes: int) -> None:
        log.info("sign-in link for %s (valid %d min, single use): %s", email, minutes, link)


class MailFailedError(Exception):
    """The mail service did not take the message."""


class ResendSender:
    """Mail through Resend's HTTP API. The key comes from the environment and is never logged."""

    URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str | None = None, transport: Any = None) -> None:
        self._key = api_key
        self.sender = sender or os.environ.get("SKY_MAIL_FROM") or "SPHEREx Space <signin@spherex.space>"
        self.transport = transport  # tests put a fake Resend here

    def send_link(self, email: str, link: str, minutes: int) -> None:
        text = (
            f"Open this link to sign in to SPHEREx Space:\n\n{link}\n\n"
            f"It works once, for {minutes} minutes. If you did not ask for it, ignore this message: nobody is signed in without the link."
        )
        page = (
            '<div style="font-family:system-ui,sans-serif;font-size:15px;line-height:1.6;color:#10202a">'
            "<p>Open this link to sign in to SPHEREx Space:</p>"
            f'<p><a href="{html.escape(link)}" style="display:inline-block;padding:10px 18px;border-radius:8px;background:#0b6b63;'
            'color:#ffffff;text-decoration:none;font-weight:600">Sign in to SPHEREx Space</a></p>'
            f"<p>It works once, for {minutes} minutes. If you did not ask for it, ignore this message: nobody is signed in without "
            "the link.</p></div>"
        )
        body = {"from": self.sender, "to": [email], "subject": "Your sign-in link for SPHEREx Space", "text": text, "html": page}
        try:
            with httpx.Client(transport=self.transport, timeout=15.0) as http:
                r = http.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:
            log.warning("sign-in mail: Resend did not answer (%s)", type(e).__name__)
            raise MailFailedError from e
        if r.status_code >= 300:
            log.warning("sign-in mail: Resend answered %d", r.status_code)  # the address and the link are not logged
            raise MailFailedError


def sender_from_env() -> MailSender:
    key = os.environ.get("SKY_RESEND_API_KEY")
    return ResendSender(key) if key else LogSender()
