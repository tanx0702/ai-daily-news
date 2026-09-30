"""LLM provider configuration helpers.

Text and image generation can use different OpenAI-compatible providers.
The newer LLM_* and IMAGE_* variables take precedence, while AGNES_* and
OPENAI_* remain supported for existing deployments.
"""

from dataclasses import dataclass
import os
from typing import Iterable, Optional


DEFAULT_TEXT_MODEL = "agnes-2.0-flash"
DEFAULT_TEXT_API_BASE = "https://apihub.agnes-ai.com/v1"
DEFAULT_IMAGE_MODEL = "agnes-image-2.1-flash"
DEFAULT_IMAGE_API_BASE = "https://apihub.agnes-ai.com"


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str


def structured_llm_request_options(config: LLMConfig) -> dict[str, object]:
    """Zhipu-specific structured-request options.

    ``glm-5.3-flash`` is a reasoning model: without ``reasoning_effort=low`` it
    spends the whole token budget on hidden reasoning and returns empty
    ``content``, which the pipeline records as an unavailable content LLM. Match
    on the final path segment so a router-namespaced id (``cbcn/glm-5.3-flash``)
    still receives the flag; matching the bare name alone silently dropped it.
    """
    model = config.model.strip().casefold()
    tail = model.rsplit("/", 1)[-1]
    if tail == "glm-5.3-flash":
        return {"extra_body": {"reasoning_effort": "low"}}
    return {}


def _clean(value: Optional[str]) -> str:
    return str(value).strip() if value is not None else ""


def _first_env(names: Iterable[str]) -> str:
    for name in names:
        value = _clean(os.environ.get(name))
        if value:
            return value
    return ""


def _first_value(
    override: Optional[str],
    env_names: Iterable[str],
    default: str = "",
    *,
    empty_override_disables: bool = False,
) -> str:
    value = _clean(override)
    if value or (override is not None and empty_override_disables):
        return value
    return _first_env(env_names) or default


def resolve_text_llm_config(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMConfig:
    return LLMConfig(
        api_key=_first_value(
            api_key,
            ("LLM_API_KEY", "AGNES_API_KEY", "OPENAI_API_KEY"),
            empty_override_disables=True,
        ),
        model=_first_value(model, ("LLM_MODEL", "AGNES_MODEL", "OPENAI_MODEL"), DEFAULT_TEXT_MODEL),
        base_url=_first_value(
            base_url,
            ("LLM_API_BASE", "AGNES_API_BASE", "OPENAI_API_BASE"),
            DEFAULT_TEXT_API_BASE,
        ),
    )


def resolve_quality_llm_config(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMConfig:
    """Resolve a dedicated review provider, falling back to the text provider."""
    text_config = resolve_text_llm_config()
    return LLMConfig(
        api_key=_first_value(
            api_key,
            ("QUALITY_LLM_API_KEY",),
            text_config.api_key,
            empty_override_disables=True,
        ),
        model=_first_value(model, ("QUALITY_LLM_MODEL",), text_config.model),
        base_url=_first_value(
            base_url,
            ("QUALITY_LLM_API_BASE",),
            text_config.base_url,
        ),
    )


def resolve_image_llm_config(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> LLMConfig:
    return LLMConfig(
        api_key=_first_value(
            api_key,
            (
                "IMAGE_API_KEY",
                "AGNES_IMAGE_API_KEY",
                "AGNES_API_KEY",
                "OPENAI_IMAGE_API_KEY",
                "OPENAI_API_KEY",
            ),
            empty_override_disables=True,
        ),
        model=_first_value(
            model,
            ("IMAGE_MODEL", "AGNES_IMAGE_MODEL", "OPENAI_IMAGE_MODEL"),
            DEFAULT_IMAGE_MODEL,
        ),
        base_url=_first_value(
            base_url,
            (
                "IMAGE_API_BASE",
                "AGNES_IMAGE_API_BASE",
                "AGNES_API_BASE",
                "OPENAI_IMAGE_API_BASE",
                "OPENAI_API_BASE",
            ),
            DEFAULT_IMAGE_API_BASE,
        ),
    )
