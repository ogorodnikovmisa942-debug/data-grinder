"""
Ограничитель расходов на одну книгу.

Потолок (PATH_BUDGET_USD, по умолчанию 0.22 $) задан в ценах вне пика, со скидкой 50%. В пиковое время та же работа стоит вдвое
дороже, поэтому потолок тогда тоже вдвое выше: иначе ограничитель урезал бы колоду из-за времени суток, а не из-за объёма работы.

Число карточек колоды Budget НЕ определяет: это цель по смыслу (path_builder.CHARS_PER_CARD, одна карточка на ~страницу).
Как работает:
  * до генерации карточек считаем прогноз стоимости при заданной цели (projected_cost) и сколько карточек вместил бы потолок
    (card_cap): если цель дороже потолка, это предупреждение в журнале и в отчёте, а не урезание колоды;
  * необязательные этапы (проверка карточек по книге, добор до цели, правка уроков) запускаются, только если
    после них останется запас на обязательные уроки;
  * всё считается по токенам и ценам DeepSeek из настроек; константы ниже измерены на пробных прогонах (см. tests/test_budget.py).
"""
import os

from .client import estimate_call_cost_usd, is_deepseek_offpeak_now

# Решение пользователя 2026-10-08: размер колоды определяют содержание и качество (source_profile.deck_goal), а не деньги. Потолок остался
# только аварийным тормозом (зациклившийся запрос, огромный материал): необязательные этапы — проверка, добор, правка уроков — при нём пропускаются.
# Книга в 450 страниц стоит ≈ 21¢ вне пика (замер/расчёт 2026-10-07); расход пользователя ограничивают квоты (app/services/quota.py).
BUDGET_USD = float(os.getenv("PATH_BUDGET_USD", "0.60"))
OPTIONAL_RESERVE_SHARE = 0.04          # доля потолка на проверку карточек по книге и правку уроков (добор идёт только из остатка: см. build_learning_path)

CHARS_PER_TOKEN = 2.8                  # русский текст; запасной расчёт, если нет данных о реальном размере запроса
BOOK_CHARS_PER_TOKEN = 3.3             # замер на «Общей теории права»: 1,135 млн знаков = 337 тыс. токенов
BOOK_CHARS_PER_NODE = 12600            # там же: 90 узлов на 1,135 млн знаков
MAP_OUT_PER_NODE = 106                 # ответ карты: ~106 токенов на узел (замер 2026-10-07 на целой книге: 19 тыс. токенов на ~180 узлов)
MAP_SECOND_SHARE = 0.4                 # какая доля книг получает вторую карту (она строится, только если первая слабая)
CARD_OUT_TOKENS = 125                  # t, d, ev, y, at, 3 дистрактора; примеров модель не пишет (замер 2026-10-05: 124; полный формат был 165)
LESSON_OUT_BASE = 330                  # узел: крючок, аналогия, вывод и один вопрос (замер: 561 токен на узел при 5,3 карточки)
LESSON_OUT_PER_CARD = 50               # модель пишет ~7 экранов на урок, а не 5 по формуле
LESSON_IN_BASE = 250                   # заголовок узла, ключи для подсветки (замер)
FACTS_PER_CARD = 3.2                   # факты «Конспекта темы» на карточку для учебника (source_profile.FACTS_PER_CARD): ≈ 1 на 700 знаков; факт в 7 раз дешевле карточки
FACT_OUT_TOKENS = 42                   # факт в ответе FACTS (замер 2026-10-07: 37–46 токенов вместе с JSON) (урок факты не пересказывает: они лежат готовым списком)
FACT_TASK_TOKENS_PER_BLOCK = 55        # строка блока в запросе FACTS: id, K и слова начала и конца
FACT_BLOCK_CHARS = 3200                # блок книги для конспекта (coverage.FACT_BLOCK_CHARS)
FACT_BATCH_BLOCKS = 60                 # блоков в одном запросе (path_builder.FACT_BATCH_BLOCKS)
LESSON_IN_PER_CARD = 110               # вопрос и ответ карточки + её доля выдержки из книги (замер: ~155 токенов на карточку вместе с базой)
LINKS_OUT_TOKENS = 1200
INTRO_IN_TOKENS = 7000
INTRO_OUT_TOKENS = 500
AUDIT_IN_PER_CARD = 650                # карточка и кусок книги (~1500 знаков)
AUDIT_OUT_PER_CARD = 90
FILL_IN_PER_SPAN = 4300                # участок книги ~12 тыс. знаков
FILL_OUT_PER_CARD = 170                # замер: добранные карточки длиннее первичных (166 токенов)


class Budget:
    def __init__(self, calls: list, limit_usd: float | None = None, offpeak: bool | None = None):
        self.calls = calls
        self.offpeak = is_deepseek_offpeak_now() if offpeak is None else offpeak
        base = BUDGET_USD if limit_usd is None else limit_usd
        self.base_limit = base
        self.limit = base * (1.0 if self.offpeak else 2.0)
        self.skipped: list[str] = []

    # --- факты -----------------------------------------------------------

    def spent(self) -> float:
        return sum(c.get("cost_usd", 0.0) for c in self.calls)

    def left(self) -> float:
        return self.limit - self.spent()

    def cost(self, hit: int = 0, miss: int = 0, out: int = 0) -> float:
        return estimate_call_cost_usd(hit, miss, out, offpeak=self.offpeak)

    def book_tokens(self, chars: int) -> int:
        """Размер книги в токенах: по реальному запросу карты (он содержит книгу), иначе по числу знаков."""
        seen = [c.get("prompt_tokens", 0) for c in self.calls if str(c.get("label", "")).startswith("map")]
        return max(seen) if seen and max(seen) > 0 else int(chars / CHARS_PER_TOKEN)

    # --- оценки этапов ----------------------------------------------------------

    def cards_reads_cost(self, book_tokens: int, batches: int) -> float:
        """Каждый запрос карточек читает книгу из кэша."""
        return self.cost(hit=book_tokens * batches)

    def lessons_cost(self, nodes: int, cards: int) -> float:
        return self.cost(miss=LESSON_IN_BASE * nodes + LESSON_IN_PER_CARD * cards,
                         out=LESSON_OUT_BASE * nodes + LESSON_OUT_PER_CARD * cards)

    def side_cost(self, book_tokens: int) -> float:
        """Связи между темами (читают книгу из кэша) и вводный урок (только карта)."""
        return self.cost(hit=book_tokens, out=LINKS_OUT_TOKENS) + self.cost(miss=INTRO_IN_TOKENS, out=INTRO_OUT_TOKENS)

    def audit_cost(self, cards: int) -> float:
        return self.cost(miss=AUDIT_IN_PER_CARD * cards, out=AUDIT_OUT_PER_CARD * cards)

    def fill_cost(self, spans: int, cards_per_span: int = 4) -> float:
        return self.cost(miss=FILL_IN_PER_SPAN * spans, out=FILL_OUT_PER_CARD * cards_per_span * spans)

    # --- решения -----------------------------------------------------------

    def facts_cost(self, facts: int) -> float:
        """Факты конспекта темы: их запись в ответе FACTS."""
        return self.cost(out=FACT_OUT_TOKENS * facts)

    def facts_overhead(self, book_tokens: int, blocks: int, calls: int) -> float:
        """Постоянная часть задачи FACTS: каждый запрос читает книгу из кэша, и в каждом есть строки блоков."""
        return self.cost(hit=book_tokens * calls, miss=FACT_TASK_TOKENS_PER_BLOCK * blocks) if calls else 0.0

    def card_cap(self, chars: int, nodes: int, batches: int, facts: bool | float = False,
                 fact_blocks: int = 0, fact_calls: int = 0) -> int:
        """Сколько карточек можно позволить, чтобы уложиться в потолок вместе с уроками, связями, фактами и запасом на проверки.
        facts: True — по FACTS_PER_CARD фактов на карточку, число — своя доля фактов на карточку, False — без них."""
        ratio = FACTS_PER_CARD if facts is True else float(facts or 0)
        bt = self.book_tokens(chars)
        fixed = (self.cards_reads_cost(bt, batches) + self.side_cost(bt) + self.lessons_cost(nodes, 0)
                 + (self.facts_overhead(bt, fact_blocks, fact_calls) if ratio else 0.0))
        per_card = self.cost(miss=LESSON_IN_PER_CARD, out=CARD_OUT_TOKENS + LESSON_OUT_PER_CARD)
        if ratio:                                          # факты идут «в нагрузку» к карточке
            per_card += self.facts_cost(1) * ratio
        room = self.left() - fixed - OPTIONAL_RESERVE_SHARE * self.limit
        return max(0, int(room / per_card))

    def projected_cost(self, chars: int, nodes: int, batches: int, cards: int, facts: bool | float = False,
                       fact_blocks: int | None = None, fact_calls: int | None = None) -> float:
        """Во сколько обойдётся весь прогон при цели в cards карточек: уже потраченное + карточки, уроки, связи, введение,
        факты и запас на проверки. Это прогноз для журнала; число карточек он не меняет."""
        ratio = FACTS_PER_CARD if facts is True else float(facts or 0)
        bt = self.book_tokens(chars)
        total = (self.spent() + self.cards_reads_cost(bt, batches) + self.side_cost(bt) + self.lessons_cost(nodes, cards)
                 + self.cost(out=CARD_OUT_TOKENS * cards) + OPTIONAL_RESERVE_SHARE * self.limit)
        blocks = fact_blocks if fact_blocks is not None else (-(-chars // FACT_BLOCK_CHARS) if ratio else 0)
        calls = fact_calls if fact_calls is not None else -(-blocks // FACT_BATCH_BLOCKS)
        return total + self.facts_cost(round(ratio * cards)) + (self.facts_overhead(bt, blocks, calls) if ratio else 0.0)

    @classmethod
    def estimate_book_cost(cls, chars: int, cards: int, offpeak: bool = True, facts: bool | float = True) -> float:
        """Прогноз стоимости книги на chars знаков при цели в cards карточек ДО запуска (расход карты оценивается по размеру книги)."""
        nodes = max(6, round(cards / 4))                 # бюджет узлов от цели: ≈ 4 карточки на узел (source_profile.node_budget)
        bt = int(chars / BOOK_CHARS_PER_TOKEN)
        probe = cls([], offpeak=offpeak)
        map_cost = probe.cost(miss=bt, out=MAP_OUT_PER_NODE * nodes) + MAP_SECOND_SHARE * probe.cost(hit=bt, out=MAP_OUT_PER_NODE * nodes)
        b = cls([{"label": "map#1", "prompt_tokens": bt, "cost_usd": map_cost}], offpeak=offpeak)
        return b.projected_cost(chars, nodes, max(1, -(-nodes // 14)), cards, facts)

    def allows(self, stage: str, estimate: float, must_keep: float = 0.0) -> bool:
        """Можно ли запускать необязательный этап: после него должно остаться must_keep (на обязательные уроки)."""
        ok = self.spent() + estimate + must_keep <= self.limit
        if not ok:
            self.skipped.append(stage)
            print(f"[Path Builder] бюджет: этап «{stage}» пропущен (~${estimate:.4f}, осталось ${self.left():.4f}, "
                  f"нужно сберечь ${must_keep:.4f})", flush=True)
        return ok

    def affordable(self, stage: str, unit_cost: float, must_keep: float, wanted: int) -> int:
        """Сколько единиц (карточек на проверку, участков на добор, узлов на правку) можно себе позволить, не залезая
        в сумму must_keep на обязательные этапы. Меньше wanted — этап урезан, ноль — пропущен (в журнале и в отчёте)."""
        if wanted <= 0:
            return 0
        n = min(wanted, int(max(0.0, self.left() - must_keep) / unit_cost)) if unit_cost > 0 else wanted
        if n < wanted:
            self.skipped.append(f"{stage}: {n} из {wanted}")
            print(f"[Path Builder] бюджет: этап «{stage}» урезан до {n} из {wanted} "
                  f"(осталось ${self.left():.4f}, нужно сберечь ${must_keep:.4f})", flush=True)
        return n

    def report(self) -> dict:
        return {"limit_usd": round(self.limit, 4), "spent_usd": round(self.spent(), 4), "offpeak": self.offpeak,
                "skipped": list(self.skipped)}


__all__ = ["Budget", "BUDGET_USD", "CARD_OUT_TOKENS"]
