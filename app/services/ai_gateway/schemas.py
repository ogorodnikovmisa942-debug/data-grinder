"""
Pydantic schemas for data validation and LLM structured responses.
"""
from typing import Optional
from pydantic import BaseModel, Field

class MnemonicSchema(BaseModel):
    keyword: str = Field(description="Ключевое слово (ассоциация) на русском языке")
    verbal_cue: str = Field(description="Сюжетная подсказка на русском языке, связывающая ключ и определение")

class CardSchema(BaseModel):
    text: str = Field(description="Лицевая сторона карточки")
    secondary_text: str = Field(description="Подсказка, пиньинь, номер статьи или сигнатура")
    translation: str = Field(description="Точный перевод или определение на русском языке")
    example: str = Field(description="Пример применения простыми понятными словами (Фейнман-стиль)")
    initial_difficulty_tier: str = Field(description="easy, medium или hard")
    mnemonic: Optional[MnemonicSchema] = None
    theme: Optional[str] = Field(default="", description="Название темы или подраздела для кластеризации")
    organ_slug: Optional[str] = Field(default="", description="Идентификатор органа, института или школы мысли")
    layer: Optional[int] = Field(default=1, description="Когнитивный слой (0: скелет, 1: основы, 2: составы, 3: развилки)")
    topological_rank: Optional[int] = Field(default=0, description="Порядковый номер изучения от корня к веткам")

class ParsedDataSchema(BaseModel):
    subject_domain: str = Field(description="language, law, code или generic")
    subject_slug: str = Field(description="Машиночитаемый код предмета в snake_case")
    phrase_title: str = Field(description="Название темы или блока карточек")
    cards: list[CardSchema]



__all__ = [
    "MnemonicSchema",
    "CardSchema",
    "ParsedDataSchema",
]
