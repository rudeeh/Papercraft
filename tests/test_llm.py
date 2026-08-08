"""Tests for LLMClient (app/services/llm.py) -- OpenRouter-only, optional per-call API key."""

from unittest.mock import MagicMock, patch

import pytest

from app.services.llm import LLMClient, LLMNotConfiguredError, OPENROUTER_BASE_URL


def _mock_openai_response(text="Generated answer."):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


class TestHasServerKey:
    def test_true_when_settings_key_present(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPENROUTER_API_KEY = "sk-or-server-key"
            client = LLMClient()
            assert client.has_server_key is True

    def test_false_when_settings_key_absent(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPENROUTER_API_KEY = None
            client = LLMClient()
            assert client.has_server_key is False


class TestGenerateResponse:
    def test_uses_server_key_when_no_override_given(self):
        with patch("app.services.llm.settings") as mock_settings, \
             patch("app.services.llm.OpenAI") as MockOpenAI:
            mock_settings.OPENROUTER_API_KEY = "sk-or-server-key"
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            MockOpenAI.return_value.chat.completions.create.return_value = _mock_openai_response()

            client = LLMClient()
            answer = client.generate_response("hello")

            assert answer == "Generated answer."
            MockOpenAI.assert_called_once_with(base_url=OPENROUTER_BASE_URL, api_key="sk-or-server-key")

    def test_per_call_key_overrides_server_key(self):
        with patch("app.services.llm.settings") as mock_settings, \
             patch("app.services.llm.OpenAI") as MockOpenAI:
            mock_settings.OPENROUTER_API_KEY = "sk-or-server-key"
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            MockOpenAI.return_value.chat.completions.create.return_value = _mock_openai_response()

            client = LLMClient()
            client.generate_response("hello", api_key="sk-or-user-supplied")

            MockOpenAI.assert_called_once_with(base_url=OPENROUTER_BASE_URL, api_key="sk-or-user-supplied")

    def test_per_call_key_works_when_no_server_key(self):
        with patch("app.services.llm.settings") as mock_settings, \
             patch("app.services.llm.OpenAI") as MockOpenAI:
            mock_settings.OPENROUTER_API_KEY = None
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            MockOpenAI.return_value.chat.completions.create.return_value = _mock_openai_response()

            client = LLMClient()
            answer = client.generate_response("hello", api_key="sk-or-user-supplied")

            assert answer == "Generated answer."

    def test_raises_when_no_key_available_at_all(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPENROUTER_API_KEY = None
            client = LLMClient()

            with pytest.raises(LLMNotConfiguredError):
                client.generate_response("hello")

    def test_blank_server_key_treated_as_absent(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.OPENROUTER_API_KEY = "   "
            client = LLMClient()

            with pytest.raises(LLMNotConfiguredError):
                client.generate_response("hello")

    def test_api_error_returns_friendly_message_not_raise(self):
        with patch("app.services.llm.settings") as mock_settings, \
             patch("app.services.llm.OpenAI") as MockOpenAI:
            mock_settings.OPENROUTER_API_KEY = "sk-or-server-key"
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            MockOpenAI.return_value.chat.completions.create.side_effect = Exception("upstream down")

            client = LLMClient()
            answer = client.generate_response("hello")

            assert "error" in answer.lower()

    def test_system_prompt_included_when_given(self):
        with patch("app.services.llm.settings") as mock_settings, \
             patch("app.services.llm.OpenAI") as MockOpenAI:
            mock_settings.OPENROUTER_API_KEY = "sk-or-server-key"
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            MockOpenAI.return_value.chat.completions.create.return_value = _mock_openai_response()

            client = LLMClient()
            client.generate_response("hello", system_prompt="Be nice.")

            call_kwargs = MockOpenAI.return_value.chat.completions.create.call_args.kwargs
            assert call_kwargs["messages"][0] == {"role": "system", "content": "Be nice."}
            assert call_kwargs["messages"][1] == {"role": "user", "content": "hello"}


class TestModelSelection:
    def test_defaults_to_settings_model(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            client = LLMClient()
            assert client.model == "openai/gpt-oss-20b:free"

    def test_explicit_model_overrides_settings(self):
        with patch("app.services.llm.settings") as mock_settings:
            mock_settings.LLM_MODEL = "openai/gpt-oss-20b:free"
            client = LLMClient(model="anthropic/claude-3.5-sonnet")
            assert client.model == "anthropic/claude-3.5-sonnet"
