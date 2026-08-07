from openai import OpenAI
from app.core.config import settings
import structlog

logger = structlog.get_logger()

class LLMClient:
    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        self.model = settings.LLM_MODEL
        
        if self.provider == "ollama":
            self.client = OpenAI(
                base_url=f"{settings.OLLAMA_BASE_URL}/v1",
                api_key="ollama", # required but ignored
            )
        elif self.provider == "openrouter":
            self.client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=settings.OPENROUTER_API_KEY,
            )
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

    def generate_response(self, prompt: str, system_prompt: str = None):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                # stream=True # Support streaming later?
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return "Sorry, I encountered an error while generating the response."

llm_client = LLMClient()
