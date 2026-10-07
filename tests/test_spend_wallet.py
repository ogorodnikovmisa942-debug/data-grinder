"""Кошелёк платных проверок: последний доллар не должен утекать из-за ошибки в скрипте."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from spend_wallet import Wallet, WalletExhausted  # noqa: E402


def test_room_keeps_a_reserve_and_spending_is_saved_after_every_charge(tmp_path):
    w = Wallet(str(tmp_path / "ledger.json"), total=1.0, reserve=0.12)
    assert w.room == pytest.approx(0.88)
    w.begin("part", cap=None)
    w.charge(0.05, "CARDS")
    w.charge(0.01, "LESSON")
    assert w.spent == pytest.approx(0.06) and w.room == pytest.approx(0.82)
    saved = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert saved["spent"] == pytest.approx(0.06) and [x["label"] for x in saved["log"]] == ["CARDS", "LESSON"]


def test_ledger_survives_between_runs(tmp_path):
    path = str(tmp_path / "ledger.json")
    first = Wallet(path, 1.0, 0.1)
    first.charge(0.3, "x")
    second = Wallet(path, 1.0, 0.1)                       # новый запуск скрипта
    assert second.spent == pytest.approx(0.3) and second.room == pytest.approx(0.6)


def test_calls_stop_when_the_wallet_is_empty(tmp_path):
    w = Wallet(str(tmp_path / "l.json"), total=0.2, reserve=0.1)
    w.begin("whole", cap=None)
    w.allow()                                              # 0.18 доступно
    w.charge(0.19, "CARDS")
    with pytest.raises(WalletExhausted):
        w.allow()                                          # дальше платных вызовов нет, как бы ни вёл себя конвейер


def test_a_step_cap_stops_only_that_step(tmp_path):
    w = Wallet(str(tmp_path / "l.json"), total=1.0, reserve=0.1)
    w.begin("notes", cap=0.03)
    w.charge(0.031, "MAP")
    with pytest.raises(WalletExhausted):
        w.allow()
    w.begin("part", cap=0.1)                               # следующий шаг начинает со своим лимитом
    w.allow()
    assert w.spent == pytest.approx(0.031)
