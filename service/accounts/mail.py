"""Sending the sign-in link. The sender is a small interface: the default prints the link in the API log (local work); a real
mail sender is plugged in with `accounts.get(st).mail = MySender()`."""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("uvicorn.error")


class MailSender(Protocol):
    def send_link(self, email: str, link: str, minutes: int) -> None:
        """Deliver the single-use sign-in link to this address."""
        ...


class LogSender:
    """No mail leaves the machine: the link is written to the API log."""

    def send_link(self, email: str, link: str, minutes: int) -> None:
        log.info("sign-in link for %s (valid %d min, single use): %s", email, minutes, link)
