import pytest
from dictionforge.text_units import split_text


def test_codepoint_ranges_duplicate_ids_quotes_decimal_and_seed_wrap():
    raw = ' \n你好🙂！ “再见。”\n你好🙂！ 3.14米。\n '
    units = split_text(raw, 'punctuation', 2**32-1, 'request')
    assert [row['text'] for row in units] == ['你好🙂！', '“再见。”', '你好🙂！', '3.14米。']
    assert [row['seed'] for row in units] == [2**32-1, 0, 1, 2]
    assert len({row['sentence_id'] for row in units}) == len(units)
    assert units == split_text(raw, 'punctuation', 2**32-1, 'request')
    assert units[0]['source_end']-units[0]['source_start'] == 4  # emoji is one code point.
    assert all(raw[row['source_start']:row['source_end']] == row['text'] for row in units)
    assert all(left['source_end'] <= right['source_start'] for left, right in zip(units, units[1:]))


def test_lines_none_and_punctuation_only_fragments_keep_raw_coordinates():
    raw = ' 甲。\r\n 乙！\n\n 丙。 '
    assert [row['text'] for row in split_text(raw, 'lines')] == ['甲。', '乙！', '丙。']
    whole = split_text(raw, 'none')[0]
    assert whole['text'] == raw.strip() == raw[whole['source_start']:whole['source_end']]
    assert [row['text'] for row in split_text('！？\n你好！\n……')] == ['你好！']


@pytest.mark.parametrize('text', ['', ' \t\n ', '……！？', '🙂', '字'*5001, '你好\x00', '你好\x1b', '你好\u202e', '坏\ud800'])
def test_invalid_text_is_rejected(text):
    with pytest.raises(ValueError, match='INVALID_TEXT'):
        split_text(text)


def test_bad_rule_and_noninteger_seed_are_rejected():
    with pytest.raises(ValueError, match='INVALID_SPLIT_RULE'):
        split_text('你好', 'words')
    with pytest.raises(ValueError, match='INVALID_SEED'):
        split_text('你好', seed=True)
