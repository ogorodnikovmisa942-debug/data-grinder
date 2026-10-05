"""
AI Gateway «Пути знаний»: DeepSeek-клиент, промпты и конвейер (книга → карта → уроки и карточки),
программный фильтр качества карточек.
"""
from .blacklist import (
    is_structurally_invalid_card,
    semantic_normalize_front,
    strip_secondary_spoilers,
)
from .json_repair import (
    extract_json_payload_with_telemetry,
    extract_json_payload,
)
from .client import (
    LLMCallError,
    LLMOutputTruncated,
    call_deepseek,
    record_ai_telemetry,
    regenerate_card_mnemonic,
    is_deepseek_offpeak_now,
    format_offpeak_start_msk,
)
from .path_prompts import PATH_BUILDER_SYSTEM_PROMPT
from .path_builder import build_learning_path, PathBuildError

__all__ = [
    "is_structurally_invalid_card",
    "semantic_normalize_front",
    "strip_secondary_spoilers",
    "extract_json_payload_with_telemetry",
    "extract_json_payload",
    "LLMCallError",
    "LLMOutputTruncated",
    "call_deepseek",
    "record_ai_telemetry",
    "regenerate_card_mnemonic",
    "is_deepseek_offpeak_now",
    "format_offpeak_start_msk",
    "PATH_BUILDER_SYSTEM_PROMPT",
    "build_learning_path",
    "PathBuildError",
]
