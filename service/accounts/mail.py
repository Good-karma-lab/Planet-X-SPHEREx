"""Sending the sign-in link. The sender is a small interface:

  LogSender      the default: no mail leaves the machine, the link is written to the API log. With it (and no dev mode) the site
                 says "Sign-in opens soon" instead of taking addresses it cannot write to.
  ResendSender   real mail through Resend (https://resend.com), used when `SKY_RESEND_API_KEY` is set.
  SmtpSender     real mail through any SMTP server (Oracle Email Delivery, another provider, our own server), used when
                 `SKY_SMTP_HOST` is set: `SKY_SMTP_PORT` (default 587: STARTTLS; 465: TLS from the start), `SKY_SMTP_USER`,
                 `SKY_SMTP_PASSWORD` (both optional for a server that takes mail without a login).
  The sender's address is `SKY_MAIL_FROM` (default "SPHEREx Space <signin@spherex.space>"; the domain must be one the mail
  service lets us send from). With both a Resend key and an SMTP host set, Resend is used.

Another sender is plugged in with `accounts.get(st).mail = MySender()`."""

from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
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


SUBJECT = "Your sign-in link for SPHEREx Space"
DEFAULT_FROM = "SPHEREx Space <signin@spherex.space>"


def message(link: str, minutes: int) -> tuple[str, str]:
    """(plain text, HTML) of the sign-in mail."""
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
    return text, page


class ResendSender:
    """Mail through Resend's HTTP API. The key comes from the environment and is never logged."""

    URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str | None = None, transport: Any = None) -> None:
        self._key = api_key
        self.sender = sender or os.environ.get("SKY_MAIL_FROM") or DEFAULT_FROM
        self.transport = transport  # tests put a fake Resend here

    def send_link(self, email: str, link: str, minutes: int) -> None:
        text, page = message(link, minutes)
        body = {"from": self.sender, "to": [email], "subject": SUBJECT, "text": text, "html": page}
        try:
            with httpx.Client(transport=self.transport, timeout=15.0) as http:
                r = http.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key}"})
        except httpx.HTTPError as e:
            log.warning("sign-in mail: Resend did not answer (%s)", type(e).__name__)
            raise MailFailedError from e
        if r.status_code >= 300:
            log.warning("sign-in mail: Resend answered %d", r.status_code)  # the address and the link are not logged
            raise MailFailedError


class SmtpSender:
    """Mail through any SMTP server. Port 465 speaks TLS from the start; any other port upgrades with STARTTLS before the login
    (a server that offers no STARTTLS is refused when a password would be sent). The password is never logged."""

    def __init__(self, host: str, port: int = 587, user: str | None = None, password: str | None = None, sender: str | None = None) -> None:
        self.host, self.port, self.user, self._password = host, port, user, password
        self.sender = sender or os.environ.get("SKY_MAIL_FROM") or DEFAULT_FROM

    def send_link(self, email: str, link: str, minutes: int) -> None:
        text, page = message(link, minutes)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, email, SUBJECT
        msg.set_content(text)
        msg.add_alternative(page, subtype="html")
        tls = ssl.create_default_context()
        try:
            smtp: smtplib.SMTP = (
                smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=tls)
                if self.port == 465
                else smtplib.SMTP(self.host, self.port, timeout=20)
            )
            with smtp:
                if self.port != 465:
                    smtp.ehlo()
                    if smtp.has_extn("starttls"):
                        smtp.starttls(context=tls)
                        smtp.ehlo()
                    elif self.user:
                        raise smtplib.SMTPException("the server offers no STARTTLS: the password is not sent in the clear")
                if self.user:
                    smtp.login(self.user, self._password or "")
                smtp.send_message(msg)
        except (smtplib.SMTPException, OSError) as e:
            log.warning("sign-in mail: the SMTP server %s:%d did not take it (%s)", self.host, self.port, type(e).__name__)
            raise MailFailedError from e


def sender_from_env() -> MailSender:
    """Resend with `SKY_RESEND_API_KEY`, else SMTP with `SKY_SMTP_HOST`, else the API log."""
    key = os.environ.get("SKY_RESEND_API_KEY")
    if key:
        return ResendSender(key)
    host = os.environ.get("SKY_SMTP_HOST")
    if host:
        try:
            port = int(os.environ.get("SKY_SMTP_PORT") or 587)
        except ValueError:
            port = 587
        return SmtpSender(host, port, os.environ.get("SKY_SMTP_USER") or None, os.environ.get("SKY_SMTP_PASSWORD") or None)
    return LogSender()
