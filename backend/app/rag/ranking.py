import math
import re
import unicodedata
from collections import Counter

IDENTIFIER = r"(?<![A-Za-z0-9_-])[A-Za-z]{1,12}-[0-9]{1,12}[A-Za-z]?(?![A-Za-z0-9_-])"
STOP_WORDS = {'什么', '怎么', '如何', '多少', '哪些', '这个', '那个', '里面', '内容', '问题', '请问', '一下', '现在', '含义', '么含'}


def tokens(text: str) -> list[str]:  # 中文使用二元词片段，编号和英文词保持完整；不额外下载分词或重排模型
    result = []
    for term in re.findall(r'[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u4e00-\u9fff]+', unicodedata.normalize('NFKC', text).lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+', term):
            pieces = [term] if len(term) <= 2 else [term[i:i + 2] for i in range(len(term) - 1)]
            result.extend(piece for piece in pieces if piece not in STOP_WORDS)
        elif term not in STOP_WORDS:
            result.append(term)
    return result


def bm25_scores(query: str, documents: dict[str, str]) -> dict[str, float]:
    terms = list(dict.fromkeys(tokens(query)))[:32]
    corpus = {key: Counter(tokens(text)) for key, text in documents.items()}
    average = sum(sum(value.values()) for value in corpus.values()) / max(1, len(corpus))
    frequencies = {term: sum(term in value for value in corpus.values()) for term in terms}
    scores = {}
    for key, counts in corpus.items():
        size, score = sum(counts.values()), 0.0
        for term in terms:
            frequency = counts[term]
            if frequency:
                idf = math.log(1 + (len(corpus) - frequencies[term] + .5) / (frequencies[term] + .5))
                score += idf * frequency * 2.5 / (frequency + 1.5 * (.25 + .75 * size / max(1, average)))
        if score > 0:
            scores[key] = score
    return scores


def exact_identifiers(query: str, text: str) -> bool:
    ids = re.findall(IDENTIFIER, query)
    return any(re.search(r'(?<![A-Za-z0-9_-])' + re.escape(item) + r'(?![A-Za-z0-9_-])', text, re.IGNORECASE) for item in ids)


def conflicting_identifier(query: str, text: str) -> bool:
    wanted = [value.lower() for value in re.findall(IDENTIFIER, query)]
    present = [value.lower() for value in re.findall(IDENTIFIER, text)]
    return bool(wanted and present and not set(wanted).intersection(present) and any(item.startswith(want) or want.startswith(item) for item in present for want in wanted))
