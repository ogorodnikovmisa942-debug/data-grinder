"""Починка ответов модели: цитаты из книги ломали JSON переносами строк и неэкранированными кавычками."""
from app.services.ai_gateway.json_repair import extract_json_payload_with_telemetry as parse


def test_valid_json_is_untouched():
    data, truncated, repaired = parse('{"nodes":[{"key":"a","cards":[{"t":"Что?","d":"Ответ.","ev":"слова из книги"}]}]}')
    assert data["nodes"][0]["cards"][0]["d"] == "Ответ." and not truncated and not repaired


def test_raw_line_break_inside_a_quote_is_accepted():
    """Цитата скопирована из PDF вместе с переносом строки и табуляцией."""
    raw = '{"nodes":[{"key":"a","cards":[{"t":"Что?","d":"Ответ.","ev":"необ-\nходимо изучать\tпредмет"}]}]}'
    data, _, _ = parse(raw)
    assert "необ-" in data["nodes"][0]["cards"][0]["ev"]


def test_unescaped_inner_quotes_are_escaped():
    raw = '{"nodes":[{"key":"a","cards":[{"t":"На какой вопрос отвечает предмет?","d":"На вопрос "что?".","ev":"предмет отвечает на вопрос "что?", а метод — "как?"","y":0}]}]}'
    data, _, repaired = parse(raw)
    card = data["nodes"][0]["cards"][0]
    assert card["d"] == 'На вопрос "что?".' and card["ev"].startswith('предмет отвечает на вопрос "что?", а метод') and card["y"] == 0
    assert repaired


def test_inner_quote_before_a_comma_and_a_word_stays_inside_the_string():
    raw = '{"a":"он сказал "да", потом ушёл","b":1}'
    data, _, _ = parse(raw)
    assert data == {"a": 'он сказал "да", потом ушёл', "b": 1}


def test_truncated_answer_is_still_repaired_to_the_last_whole_card():
    raw = '{"cards":[{"t":"1?","d":"1."},{"t":"2?","d":"2."},{"t":"3?","d":"обор'
    data, truncated, repaired = parse(raw)
    assert truncated and repaired and len(data["cards"]) == 2


def test_hopeless_text_still_raises():
    import pytest
    with pytest.raises(ValueError):
        parse("это не JSON вообще")


def test_array_closed_with_a_curly_brace_is_repaired_like_the_real_failure_of_2026_10_07():
    """Настоящий сбой: ключевые мысли "g" закрыты «}» вместо «]», дальше лишние скобки."""
    raw = ('{"nodes":[{"key":"metodologiya","cards":[{"t":"Что?","d":"Ответ.","ev":"слова","y":0,"at":"term","x":["а","б","в"]}],'
           '"g":["Первая мысль.","Вторая [в скобках] мысль.","Третья мысль."}]}]}')
    data, truncated, repaired = parse(raw)
    node = data["nodes"][0]
    assert repaired and node["key"] == "metodologiya" and node["g"][1] == "Вторая [в скобках] мысль." and len(node["cards"]) == 1


def test_brackets_inside_strings_are_not_touched_by_the_repair():
    from app.services.ai_gateway.json_repair import _fix_brackets
    ok = '{"a":"текст с } и ] внутри","b":[1,2]}'
    assert _fix_brackets(ok) == ok


def test_missing_opening_quote_of_a_key_is_restored_like_the_real_lesson_failure():
    """Настоящий сбой урока: «,emo":"talk"» вместо «,"emo":"talk"»."""
    raw = ('{"nodes":[{"key":"sluzhba","lesson":{"screens":[{"say":"Работа в прокуратуре — служба.",emo":"talk","focus":["prokuratura"]},'
           '{"say":"Помнишь прокуратуру?","emo":"think","focus":[]}],"check":[]}}]}')
    data, _, repaired = parse(raw)
    screens = data["nodes"][0]["lesson"]["screens"]
    assert repaired and screens[0]["emo"] == "talk" and screens[1]["emo"] == "think" and len(screens) == 2
