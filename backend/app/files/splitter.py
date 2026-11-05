from typing import TypedDict  # 描述切片字段，便于调用时检查类型

CHUNK_SIZE = 1000  # 每块最多1000个字符，不是字节或Token
CHUNK_OVERLAP = 150  # 相邻块重复150个字符

class TextChunk(TypedDict):
    chunk_index: int  # 文件内从0开始的序号
    content: str  # 本块的原始文字，保留空格和换行
    start_offset: int  # 起始字符位置，包含
    end_offset: int  # 结束字符位置，不包含

def split_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[TextChunk]:  # 纯文本切片，不读写数据库
    if type(chunk_size) is not int or chunk_size <= 0:
        raise ValueError("chunk_size必须是正整数")
    if type(overlap) is not int or not 0 <= overlap < chunk_size:
        raise ValueError("overlap必须大于或等于0，且小于chunk_size")
    if not text.strip():
        return []
    chunks: list[TextChunk] = []  # 按原文顺序保存的切片
    start = 0  # 本块起始位置
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append({"chunk_index": len(chunks), "content": text[start:end], "start_offset": start, "end_offset": end})
        if end == len(text):
            break
        start = end - overlap  # 回退重叠部分，再开始下一块
    return chunks
