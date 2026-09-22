"""
AI Gateway Package for Data Grinder.
Provides multi-LLM parsing, prompt caching, cognitive decomposition, schema validation, and card filtering.
"""
from .prompts import (
    DEEPSEEK_CACHED_SYSTEM_PROMPT,
    CURRICULUM_SKELETON_SYSTEM_PROMPT,
    build_granularity_prompt,
)
from .schemas import (
    MnemonicSchema,
    CardSchema,
    ParsedDataSchema,
)
from .blacklist import (
    is_blacklisted_card,
    semantic_normalize_front,
    BLACKLISTED_PATTERNS,
)
from .json_repair import (
    extract_json_payload_with_telemetry,
    extract_json_payload,
    unpack_minified_cards,
)
from .chunker import (
    analyze_source_density,
    split_text_into_chunks,
    extract_curriculum_skeleton,
)
from .client import (
    call_deepseek,
    call_gemini,
    parse_raw_text,
    regenerate_card_mnemonic,
    is_failover_error,
    record_ai_telemetry,
)

__all__ = [
    "DEEPSEEK_CACHED_SYSTEM_PROMPT",
    "CURRICULUM_SKELETON_SYSTEM_PROMPT",
    "build_granularity_prompt",
    "MnemonicSchema",
    "CardSchema",
    "ParsedDataSchema",
    "is_blacklisted_card",
    "semantic_normalize_front",
    "BLACKLISTED_PATTERNS",
    "extract_json_payload_with_telemetry",
    "extract_json_payload",
    "unpack_minified_cards",
    "analyze_source_density",
    "split_text_into_chunks",
    "extract_curriculum_skeleton",
    "call_deepseek",
    "call_gemini",
    "parse_raw_text",
    "regenerate_card_mnemonic",
    "is_failover_error",
    "record_ai_telemetry",
]
