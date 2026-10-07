"""
DeepSeek-клиент: вызов модели (JSON Mode + Context Caching), учёт стоимости, окно скидок, телеметрия.
"""
import asyncio
from datetime import datetime, timezone

import httpx

from app.core.config import settings
from .json_repair import extract_json_payload_with_telemetry, extract_json_payload


class LLMCallError(RuntimeError):
    """Сбой после оплаченного ответа модели: meta несёт токены и стоимость для учёта расходов."""

    def __init__(self, message: str, meta: dict | None = None):
        super().__init__(message)
        self.meta = meta or {}


class LLMOutputTruncated(LLMCallError):
    """Модель упёрлась в max_tokens: нужен меньший объём задачи или больший лимит, а не повтор."""


def is_deepseek_offpeak_now(now_utc: datetime | None = None) -> bool:
    """Пик DeepSeek: пн–пт 01:00–04:00 и 06:00–10:00 UTC. Всё остальное время — скидка 50%."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        return True
    return not (1 <= now_utc.hour < 4 or 6 <= now_utc.hour < 10)


def next_offpeak_start(now_utc: datetime | None = None) -> datetime:
    """Момент (UTC), когда начнётся ближайшее окно скидки; если скидка уже идёт — текущий момент."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if is_deepseek_offpeak_now(now_utc):
        return now_utc
    end_hour = 4 if now_utc.hour < 4 else 10
    return now_utc.replace(hour=end_hour, minute=0, second=0, microsecond=0)


def format_offpeak_start_msk(now_utc: datetime | None = None) -> str:
    """«13:00 по МСК» — для сообщений пользователю об отложенной нарезке."""
    start = next_offpeak_start(now_utc)
    return f"{(start.hour + 3) % 24:02d}:{start.minute:02d} по МСК"


# deepseek-v4-pro дороже flash: вход 4.4×, выход 3.3× (тарифы из docs DeepSeek, пиковые: 1.32 / 0.044 / 3.96 $ за 1 млн)
_PRO_MULT = {"hit": 7.33, "miss": 4.4, "out": 3.3}


def estimate_call_cost_usd(cache_hit_tokens: int, cache_miss_tokens: int, output_tokens: int,
                           offpeak: bool | None = None, model: str | None = None) -> float:
    """Стоимость одного вызова по ценам deepseek-flash из настроек (пиковым; вне пика — скидка 50%)."""
    if offpeak is None:
        offpeak = is_deepseek_offpeak_now()
    factor = 0.5 if offpeak else 1.0
    m = _PRO_MULT if model == MODEL_FALLBACK else {"hit": 1, "miss": 1, "out": 1}
    cost = (
        cache_hit_tokens * settings.DEEPSEEK_PRICE_CACHE_HIT * m["hit"]
        + cache_miss_tokens * settings.DEEPSEEK_PRICE_CACHE_MISS * m["miss"]
        + output_tokens * settings.DEEPSEEK_PRICE_OUTPUT * m["out"]
    ) / 1_000_000
    return round(cost * factor, 6)


async def record_ai_telemetry(
    job_id: str | None,
    user_id: str,
    model_requested: str,
    model_resolved: str,
    input_chars: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cache_hit: bool = False,
    is_truncated: bool = False,
    repair_successful: bool = False,
    cards_generated: int = 0,
    duration_ms: int = 0,
    status: str = "success",
    error_message: str | None = None,
    cache_hit_tokens: int = 0,
    cost_usd: float = 0.0
):
    """Асинхронная безопасная запись в таблицу ai_telemetry_logs."""
    try:
        from app.database.session import AsyncSessionLocal
        from app.database.models import AiTelemetryLog
        async with AsyncSessionLocal() as db:
            log = AiTelemetryLog(
                job_id=str(job_id) if job_id is not None else None,
                user_id=user_id or "default_user",
                model_requested=model_requested,
                model_resolved=model_resolved,
                input_chars=input_chars,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cache_hit=cache_hit,
                is_truncated=is_truncated,
                repair_successful=repair_successful,
                cards_generated=cards_generated,
                duration_ms=duration_ms,
                status=status,
                error_message=error_message,
                cache_hit_tokens=cache_hit_tokens,
                cost_usd=cost_usd
            )
            db.add(log)
            await db.commit()
    except Exception as log_err:
        print(f"[AI Gateway WARN] Сбой сохранения телеметрии в БД: {log_err}")



# Модели DeepSeek (сентябрь 2026): deepseek-flash (V4.1 Flash) — рабочая, deepseek-v4-pro — в 3–4 раза дороже, держим как страховку.
# Старые deepseek-chat / deepseek-reasoner отключены 24.07.2026 и больше не отвечают.
MODEL_MAIN = "deepseek-flash"
MODEL_FALLBACK = "deepseek-v4-pro"


def _debug_dump_raw(content: str | None) -> None:
    """Сырой ответ, который не удалось разобрать, — в файл, если задана PATH_DEBUG_RAW_DIR (для диагностики; по умолчанию выключено)."""
    import os
    import time
    folder = os.getenv("PATH_DEBUG_RAW_DIR")
    if not folder:
        return
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"raw_failed_{int(time.time() * 1000)}.txt"), "w", encoding="utf-8") as f:
            f.write(content or "")
    except OSError:
        pass


async def call_deepseek(
    user_prompt: str,
    system_instruction: str,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    temperature: float = 0.1,
    model: str | None = None,
    thinking: bool = False,
) -> tuple[dict, dict]:
    """Вызывает DeepSeek (JSON Mode + Context Caching). Возвращает (распарсенный JSON, метрики с токенами и стоимостью).
    Оплаченный, но непригодный ответ поднимает LLMCallError с метриками, чтобы расход не терялся."""
    api_key = (settings.DEEPSEEK_API_KEY or "").strip().strip('"\'')
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")

    base_url = settings.DEEPSEEK_BASE_URL.rstrip('/')
    url = f"{base_url}/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    target_model = model or settings.DEEPSEEK_MODEL or MODEL_MAIN

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "max_tokens": max_tokens or 32768,
        # Без «обдумывания» ответ короче и дешевле; для основ и тем включаем его точечно (thinking=True).
        # В режиме обдумывания температура игнорируется, а токены рассуждений входят в max_tokens и тарифицируются как вывод.
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }

    print(f"[AI Gateway / DeepSeek] Вызов модели: {target_model} (Prompt Caching enabled)...")
    resolved_model = target_model
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Жёсткий предел: timeout httpx — пауза между байтами, а перегруженный DeepSeek держит соединение
        # пустыми строками до 10 минут. Без wait_for вызов «висел» бы без ответа и без счёта.
        response = await asyncio.wait_for(client.post(url, headers=headers, json=payload), timeout)

        # Автоматический fallback: запрошенная модель недоступна/не найдена — пробуем страховочную deepseek-v4-pro
        if response.status_code in (400, 404) and target_model != MODEL_FALLBACK:
            print(f"[AI Gateway / DeepSeek WARNING] Модель '{target_model}' вернула код {response.status_code}. Пробуем '{MODEL_FALLBACK}'...")
            payload["model"] = MODEL_FALLBACK
            resolved_model = MODEL_FALLBACK
            response = await asyncio.wait_for(client.post(url, headers=headers, json=payload), timeout)

        if response.status_code == 200:
            data = response.json()
            
            # Логируем метрики эффективности кэширования DeepSeek
            usage = data.get("usage") or {}
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("Ответ от DeepSeek API не содержит choices.")

            cache_hit_tokens = usage.get("prompt_cache_hit_tokens", 0)
            cache_miss_tokens = usage.get("prompt_cache_miss_tokens", 0)
            prompt_tokens = usage.get("prompt_tokens", cache_hit_tokens + cache_miss_tokens)
            output_tokens = usage.get("completion_tokens", 0)
            cache_hit = cache_hit_tokens > 0
            print(f"[DeepSeek Metrics] Кэш-хит: {cache_hit_tokens} токенов (~$0.003-0.006/1M) | Мисс: {cache_miss_tokens} токенов | Вывод: {output_tokens} токенов | Модель: {resolved_model}")

            content = choices[0]["message"]["content"]
            finish_reason = choices[0].get("finish_reason")
            meta = {
                "cache_hit_tokens": cache_hit_tokens,
                "cache_miss_tokens": cache_miss_tokens,
                "cost_usd": estimate_call_cost_usd(cache_hit_tokens, cache_miss_tokens, output_tokens, model=resolved_model),
                "finish_reason": finish_reason,
                "model_requested": target_model,
                "model_resolved": resolved_model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": output_tokens,
                "cache_hit": cache_hit,
                "is_truncated": False,
                "repair_successful": False
            }
            if finish_reason == "length":
                meta["raw_head"] = (content or "")[:1500]
                meta["raw_tail"] = (content or "")[-3000:]
                # Обрезанный по лимиту ответ бессмысленно повторять тем же запросом
                raise LLMOutputTruncated(
                    f"Ответ обрезан на лимите max_tokens={payload['max_tokens']} (вывод {output_tokens} токенов)", meta
                )
            try:
                raw_payload, is_truncated, repair_successful = extract_json_payload_with_telemetry(content)
            except ValueError as parse_err:
                meta["raw_head"] = (content or "")[:1500]
                meta["raw_tail"] = (content or "")[-1500:]
                _debug_dump_raw(content)
                raise LLMCallError(str(parse_err), meta) from parse_err
            meta["is_truncated"] = is_truncated
            meta["repair_successful"] = repair_successful
            return raw_payload, meta
        else:
            print(f"[AI Gateway / DeepSeek ERROR] Код {response.status_code}: {response.text}")
            raise RuntimeError(f"DeepSeek API error ({response.status_code}): {response.text}")



async def regenerate_card_mnemonic(text: str, translation: str, subject: str, preference: str = "visual") -> dict:
    pref_style = "визуальные и структурные ассоциации (графемы, форма, код)" if preference == "visual" else "акустические и сюжетные созвучия"
    prompt = f"""Сгенерируй яркую русскую мнемонику для запоминания:
Термин: {text}
Значение: {translation}
Предмет: {subject}
Стиль ассоциации: {pref_style}

Верни строгий JSON:
{{"keyword": "Ключевое слово", "verbal_cue": "Сюжетная связка ключа и значения"}}"""

    system_instruction = "You are an expert mnemonic generator. Return strictly a raw JSON object with 'keyword' and 'verbal_cue'. No markdown."

    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY не установлен в .env")
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    target_model = settings.DEEPSEEK_MODEL or "deepseek-flash"
    fallback_model = MODEL_FALLBACK

    payload = {
        "model": target_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt}
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.3
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        res = await client.post(url, headers=headers, json=payload)
        if res.status_code in (400, 404) and payload["model"] != fallback_model:
            payload["model"] = fallback_model
            res = await client.post(url, headers=headers, json=payload)
        if res.status_code == 200:
            choices = res.json().get("choices") or []
            if not choices:
                raise ValueError("Ответ от модели не содержит choices.")
            content = choices[0]["message"]["content"]
            return extract_json_payload(content)
        else:
            raise RuntimeError(f"DeepSeek mnemonic error ({res.status_code}): {res.text}")



__all__ = [
    "LLMCallError",
    "LLMOutputTruncated",
    "is_deepseek_offpeak_now",
    "next_offpeak_start",
    "format_offpeak_start_msk",
    "estimate_call_cost_usd",
    "record_ai_telemetry",
    "call_deepseek",
    "regenerate_card_mnemonic",
]
