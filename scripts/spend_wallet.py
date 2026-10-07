"""Кошелёк для платных проверок: накопленный расход между запусками, остановка вызовов при исчерпании, лимит на шаг."""
import json
import time
from pathlib import Path


class WalletExhausted(RuntimeError):
    pass


class Wallet:
    """total — сколько всего долларов на балансе; reserve — доля, которую не трогаем (учёт идёт по токенам, это оценка, а не счёт провайдера)."""

    def __init__(self, path: str, total: float, reserve: float):
        self.path, self.total, self.reserve = Path(path), total, reserve
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"spent": 0.0, "log": []}
        self.step, self.step_spent, self.step_cap = "", 0.0, None

    @property
    def spent(self) -> float:
        return float(self.data["spent"])

    @property
    def room(self) -> float:
        return self.total * (1 - self.reserve) - self.spent

    def begin(self, step: str, cap: float | None) -> None:
        self.step, self.step_spent, self.step_cap = step, 0.0, cap

    def allow(self) -> None:
        """Вызывается перед КАЖДЫМ платным вызовом."""
        if self.room <= 0:
            raise WalletExhausted(f"кошелёк исчерпан: потрачено ${self.spent:.4f} из ${self.total:.2f} (запас {self.reserve:.0%})")
        if self.step_cap is not None and self.step_spent >= self.step_cap:
            raise WalletExhausted(f"шаг «{self.step}» достиг лимита ${self.step_cap:.3f}")

    def charge(self, usd: float, label: str = "") -> None:
        """Вызывается после каждого вызова и пишет файл сразу: при падении скрипта учёт не теряется."""
        usd = float(usd or 0.0)
        self.data["spent"] = round(self.spent + usd, 6)
        self.step_spent += usd
        self.data["log"].append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "step": self.step, "label": label[:40], "usd": round(usd, 6)})
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
