"""Ограничитель расходов: потолок в ценах вне пика, число карточек по карману, допуск необязательных этапов."""
from app.services.ai_gateway.budget import Budget, BUDGET_USD, CARD_OUT_TOKENS

MAP_CALL = {"label": "map#1", "prompt_tokens": 340_000, "cost_usd": 0.08}


def test_ceiling_is_given_off_peak_and_doubles_at_peak():
    off, peak = Budget([], offpeak=True), Budget([], offpeak=False)
    assert off.limit == BUDGET_USD and peak.limit == 2 * BUDGET_USD
    assert off.cost(out=1_000_000) * 2 == peak.cost(out=1_000_000)       # та же работа в пик вдвое дороже


def test_book_size_comes_from_the_real_map_request_with_a_fallback():
    assert Budget([MAP_CALL], offpeak=True).book_tokens(1_000_000) == 340_000
    assert Budget([], offpeak=True).book_tokens(2_800_000) == 1_000_000    # нет запросов: ~2.8 знака на токен
    assert Budget([{"label": "links#1", "prompt_tokens": 5, "cost_usd": 0}], offpeak=True).book_tokens(2_800) == 1000


def test_typical_book_gets_a_few_hundred_cards_within_the_ceiling():
    b = Budget([MAP_CALL], offpeak=True)
    cap = b.card_cap(1_135_000, nodes=80, batches=8)
    assert 350 <= cap <= 700                                              # ~450 страниц при потолке 18 центов, «лёгкие» карточки и уроки
    # Всё, что запланировано, в сумме не превышает потолок
    total = (b.spent() + b.cards_reads_cost(340_000, 8) + b.side_cost(340_000) + b.lessons_cost(80, cap)
             + b.cost(out=CARD_OUT_TOKENS * cap))
    assert total <= b.limit


def test_more_already_spent_means_fewer_cards_and_nothing_below_zero():
    cheap = Budget([{**MAP_CALL, "cost_usd": 0.05}], offpeak=True).card_cap(1_000_000, 80, 8)
    dear = Budget([{**MAP_CALL, "cost_usd": 0.10}], offpeak=True).card_cap(1_000_000, 80, 8)
    broke = Budget([{**MAP_CALL, "cost_usd": 0.50}], offpeak=True).card_cap(1_000_000, 80, 8)
    assert cheap > dear > broke == 0


def test_optional_stage_is_trimmed_or_skipped_by_what_is_left_after_the_reserve(capsys):
    b = Budget([{**MAP_CALL, "cost_usd": 0.17}], offpeak=True)             # осталось 0.01
    unit = b.audit_cost(1)
    assert b.affordable("проверка карточек", unit, must_keep=0.0, wanted=5) == 5
    n = b.affordable("проверка карточек", unit, must_keep=0.0095, wanted=60)
    assert 0 <= n < 60                                                    # урезано: нужно сберечь деньги на уроки
    assert b.affordable("добор участков", b.fill_cost(1), must_keep=0.02, wanted=3) == 0
    assert any("добор участков" in s for s in b.skipped) and "добор участков" in capsys.readouterr().out
    assert b.report()["skipped"] == b.skipped and b.report()["limit_usd"] == b.limit


def test_allows_checks_the_estimate_against_the_ceiling():
    b = Budget([{**MAP_CALL, "cost_usd": 0.17}], offpeak=True)
    assert b.allows("правка", 0.005) and not b.allows("правка", 0.02) and b.skipped == ["правка"]
