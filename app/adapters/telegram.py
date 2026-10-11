from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx

from app.adapters.publisher_base import PublishResult, SocialPublisher
from app.config import get_settings


class TelegramPublisher(SocialPublisher):
    """Real platform adapter for publishing to Telegram channels/chats via Bot API."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        timeout: float = 10.0,
    ):
        settings = get_settings()
        self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.timeout = timeout

    @property
    def platform_name(self) -> str:
        return "telegram"

    def _sanitize(self, text: str) -> str:
        """Ensure bot token never leaks into error messages or logs."""
        if self.bot_token and self.bot_token in text:
            return text.replace(self.bot_token, "[REDACTED_BOT_TOKEN]")
        return text

    def publish(
        self,
        content: str,
        **kwargs: Any,
    ) -> PublishResult:
        """Send message via Telegram Bot API sendMessage endpoint.

        Validates configuration, constructs payload, handles timeouts,
        and parses Telegram message_id.
        """
        now = datetime.now(timezone.utc)
        idempotency_key = kwargs.get("idempotency_key")

        # 1. Validate configuration
        if not self.bot_token or not self.chat_id:
            return PublishResult(
                success=False,
                platform="telegram",
                error="Telegram configuration missing: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set.",
                idempotency_key=idempotency_key,
            )

        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": content,
        }

        # 2. Perform HTTP request
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(api_url, json=payload)

        except httpx.TimeoutException:
            return PublishResult(
                success=False,
                platform="telegram",
                error=f"Telegram API request timed out after {self.timeout} seconds.",
                idempotency_key=idempotency_key,
                is_uncertain=True,
            )
        except httpx.RequestError as e:
            return PublishResult(
                success=False,
                platform="telegram",
                error=f"Telegram network connection error: {self._sanitize(str(e))}",
                idempotency_key=idempotency_key,
                is_uncertain=True,
            )

        # 3. Parse and validate JSON response
        try:
            data = response.json()
        except Exception:
            return PublishResult(
                success=False,
                platform="telegram",
                error=f"Malformed response from Telegram (status {response.status_code}). Not valid JSON.",
                idempotency_key=idempotency_key,
                is_uncertain=response.status_code >= 500,
            )

        # 4. Handle Telegram response
        if response.status_code == 200 and data.get("ok") is True:
            result_obj = data.get("result", {})
            message_id = str(result_obj.get("message_id", ""))

            preview = (
                f"--- [Telegram Chat {self.chat_id} Message #{message_id}] ---\n"
                f"{content}\n"
                f"--- (Delivered via Telegram Bot API) ---"
            )

            return PublishResult(
                success=True,
                platform="telegram",
                external_post_id=message_id,
                published_at=now,
                preview=preview,
                error=None,
                idempotency_key=idempotency_key,
                raw_response=data,
            )

        # 5. Handle Telegram API errors
        error_desc = data.get("description", f"HTTP {response.status_code}")
        sanitized_error = self._sanitize(error_desc)
        return PublishResult(
            success=False,
            platform="telegram",
            error=f"Telegram API error ({response.status_code}): {sanitized_error}",
            idempotency_key=idempotency_key,
            raw_response=data,
        )
