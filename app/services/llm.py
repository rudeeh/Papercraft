from typing import Optional

from openai import OpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger()

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMNotConfiguredError(RuntimeError):
    """
    Raised when no OpenRouter API key is available -- neither configured on
    the server (``OPENROUTER_API_KEY``) nor supplied with the request.
    Callers (API routes) should catch this and surface a clear "provide an
    API key" response rather than a generic error.
    """


class LLMClient:
    """
    OpenRouter-backed LLM client.

    Accepts a per-call ``api_key`` override so deployments without a
    server-configured ``OPENROUTER_API_KEY`` can still work -- e.g. a user
    entering their own key in the frontend, sent with each request.
    """

    def __init__(self, model: Optional[str] = None):
        self.model = model or settings.LLM_MODEL

    @property
    def has_server_key(self) -> bool:
        return bool(settings.OPENROUTER_API_KEY)

    def generate_response(
        self, prompt: str, system_prompt: str = None, api_key: Optional[str] = None,
    ) -> str:
        key = (api_key or settings.OPENROUTER_API_KEY or "").strip()
        if not key:
            raise LLMNotConfiguredError(
                "No OpenRouter API key configured. Set OPENROUTER_API_KEY on "
                "the server, or provide one with this request."
            )

        client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return "Sorry, I encountered an error while generating the response."

llm_client = LLMClient()
