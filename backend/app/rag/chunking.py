from backend.app.files.splitter import CHUNK_OVERLAP, CHUNK_SIZE, TextChunk, split_text
import hashlib

CHUNKING_VERSION = "token-window-v2"  # 策略变化后旧索引需要重建，避免继续使用被静默截断的向量
TARGET_TOKENS = 384  # 默认给512 Token模型留出特殊符号和边界余量
OVERLAP_TOKENS = 48


def chunk_policy_key(model_name: str) -> str:
    value = f'{CHUNKING_VERSION}\n{model_name}\n{TARGET_TOKENS}\n{OVERLAP_TOKENS}\n{CHUNK_SIZE}\n{CHUNK_OVERLAP}'
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def chunk_content_key(text: str, model_name: str) -> str:
    return hashlib.sha256((chunk_policy_key(model_name) + '\n' + text).encode('utf-8')).hexdigest()

def split_with_tokenizer(text: str, tokenizer, max_seq_length: int) -> list[TextChunk]:  # 保留原文字符位置，不通过解码Token重写正文
    special = tokenizer.num_special_tokens_to_add(pair=False)
    if type(max_seq_length) is not int or max_seq_length - special <= 8:
        raise ValueError("向量模型的输入长度不支持当前切片策略")
    size = min(TARGET_TOKENS, max_seq_length - special - 8)
    overlap = min(OVERLAP_TOKENS, size // 4)
    chunks = []
    # 先用有重叠的字符窗口限制分词内存，再按Token细分；不会丢掉窗口之间的内容。
    for window in split_text(text):
        encoded = tokenizer(window["content"], add_special_tokens=False, truncation=False, return_offsets_mapping=True, verbose=False)
        offsets = [(start, end) for start, end in encoded["offset_mapping"] if end > start]
        start = 0
        while start < len(offsets):
            end = min(start + size, len(offsets))
            local_start = offsets[start][0] if start else 0
            local_end = offsets[end][0] if end < len(offsets) else len(window["content"])
            absolute_start = window["start_offset"] + local_start
            absolute_end = window["start_offset"] + local_end
            chunks.append({"chunk_index": len(chunks), "content": text[absolute_start:absolute_end], "start_offset": absolute_start, "end_offset": absolute_end})
            if end == len(offsets):
                break
            start = end - overlap
    chunks.sort(key=lambda item: (item["start_offset"], item["end_offset"]))
    for index, chunk in enumerate(chunks):
        chunk["chunk_index"] = index
    return chunks
