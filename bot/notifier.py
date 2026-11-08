"""Telegram alerts (optional). Never raises: an alert failure must not stop the bot."""
import logging

import requests

log = logging.getLogger("notify")


class Notifier:
    def __init__(self, token="", chat_id=""):
        self.token, self.chat_id = token, chat_id

    def send(self, text):
        log.info("ALERT: %s", text)
        if not (self.token and self.chat_id):
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": text[:4000]}, timeout=10)
        except Exception as e:  # noqa: BLE001
            log.warning("telegram failed: %s", e)
