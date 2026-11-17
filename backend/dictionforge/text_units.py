"""Deterministic speech units with Unicode code-point source coordinates."""
import unicodedata
import uuid

BOUNDARIES = set('。！？!?；;.…')
CLOSERS = set('”’」』）》】〉〕〗〙〛\"\'')


def validate_text(text):
    if not isinstance(text, str) or not 1 <= len(text) <= 5000:
        raise ValueError('INVALID_TEXT：请输入 1–5000 个字符')
    if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs'} and char not in '\n\r\t\u200c\u200d' for char in text):
        raise ValueError('INVALID_TEXT_CONTROL：文本包含不可用控制字符')
    if not any(unicodedata.category(char)[0] in {'L', 'N'} for char in text):
        raise ValueError('INVALID_TEXT：文本须包含文字或数字，不能只有空白或标点')
    return text


def split_text(text, split_rule='punctuation', seed=42, namespace=''):
    validate_text(text)
    if split_rule not in {'punctuation', 'lines', 'none'}:
        raise ValueError('INVALID_SPLIT_RULE')
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('INVALID_SEED')
    spans = []
    if split_rule == 'none':
        spans = [(0, len(text))]
    elif split_rule == 'lines':
        start = 0
        for part in text.splitlines(keepends=True):
            spans.append((start, start+len(part)))
            start += len(part)
    else:
        start = cursor = 0
        while cursor < len(text):
            char = text[cursor]
            decimal = char == '.' and cursor > 0 and cursor+1 < len(text) and text[cursor-1].isdigit() and text[cursor+1].isdigit()
            if (char in BOUNDARIES and not decimal) or char in '\r\n':
                end = cursor+1
                while end < len(text) and (text[end] in BOUNDARIES or text[end] in CLOSERS):
                    end += 1
                spans.append((start, end))
                start = cursor = end
            else:
                cursor += 1
        if start < len(text):
            spans.append((start, len(text)))
    result = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end-1].isspace():
            end -= 1
        sentence = text[start:end]
        if not any(unicodedata.category(char)[0] in {'L', 'N'} for char in sentence):
            continue
        index = len(result)
        sentence_id = uuid.uuid5(uuid.NAMESPACE_URL, f'dictionforge:sentence:{namespace}:{index}:{start}:{end}:{sentence}').hex
        result.append(dict(sentence_id=sentence_id, index=index, text=sentence, source_start=start,
                           source_end=end, seed=(seed+index) % 2**32))
    if not result:
        raise ValueError('INVALID_TEXT：分句后没有可合成文字')
    return result
