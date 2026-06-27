import os

from .anthropic_messages import AnthropicProvider
from .base import ModelProvider, ModelResponse, ProviderError, ToolCall, Usage
from .gemini_native import GeminiProvider
from .openai_compat import OpenAICompatProvider
from .openai_responses import ResponsesProvider

PROVIDERS: dict[str, type] = {
    "chat": OpenAICompatProvider,  # universal wire format; local/OSS backends
    "responses": ResponsesProvider,  # OpenAI native: visible+carried reasoning
    "anthropic": AnthropicProvider,  # Claude native: thinking, caching, typed tool inputs
    "gemini": GeminiProvider,  # Gemini native: thought summaries, exact thinking tokens
}


def auto_api(base_url_flag: str | None) -> str:
    """Pick the best wire format the backend is known to speak.

    Anthropic backends get the native messages api (thinking, caching, typed
    tool inputs). Everything else gets chat completions, the universal format.
    OpenAI's responses api stays explicit opt-in: its reasoning request is
    rejected by non-reasoning models, and the model's capability cannot be
    detected from its name.
    """
    base_url = base_url_flag or os.environ.get("STS_BENCH_BASE_URL")
    if base_url is not None:
        if "api.anthropic.com" in base_url:
            return "anthropic"
        if "generativelanguage.googleapis.com" in base_url:
            return "gemini"
        return "chat"
    if os.environ.get("STS_BENCH_API_KEY"):
        return "chat"  # explicit key for an unnamed backend: assume compat
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "chat"


__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "ModelProvider",
    "ModelResponse",
    "OpenAICompatProvider",
    "PROVIDERS",
    "ProviderError",
    "ResponsesProvider",
    "ToolCall",
    "Usage",
    "auto_api",
]
