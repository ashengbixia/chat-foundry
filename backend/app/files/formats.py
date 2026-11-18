"""前后端共同使用formats.json，不依据客户端MIME类型绕过扩展名校验。"""
import json
from pathlib import Path

FORMATS = json.loads(Path(__file__).with_name('formats.json').read_text(encoding='utf-8'))
SUPPORTED_EXTENSIONS = frozenset(extension for group in FORMATS['groups'] for extension in group['extensions'])
TEXT_EXTENSIONS = frozenset(extension for group in FORMATS['groups'] if group['parser'] == 'text' for extension in group['extensions'])
MAX_FILE_SIZE = FORMATS['max_upload_bytes']
MAX_TEXT_CHARS = FORMATS['max_text_chars']
UNSUPPORTED_MESSAGE = FORMATS['unsupported_message']
