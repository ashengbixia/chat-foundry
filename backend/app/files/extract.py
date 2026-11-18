"""常见文档正文提取；不执行宏、公式、脚本或访问文档中的外部链接。"""
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from backend.app.files.formats import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, MAX_TEXT_CHARS, UNSUPPORTED_MESSAGE


class ExtractionError(ValueError):
    pass


def check_office_archive(content):
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 30 * 1024 * 1024:
                raise ExtractionError('文档解压后的内容过大，请拆分后上传')
            if any(item.flag_bits & 1 or item.filename.lower().endswith('vbaproject.bin') for item in entries):
                raise ExtractionError('不支持加密或包含宏的Office文档，请转换后上传')
    except BadZipFile:
        raise ExtractionError('不是有效的Office文档，请检查文件内容与扩展名') from None


def extract_document(content: bytes, extension: str) -> str:
    parts, length = [], 0

    def add(value):
        nonlocal length
        if not isinstance(value, str):
            value = str(value)
        if value.strip():
            length += len(value) + 1
            if length > MAX_TEXT_CHARS:
                raise ExtractionError('提取正文超过100万字符，请拆分文件后上传')
            parts.append(value)

    extension = extension.lower()
    if extension in TEXT_EXTENSIONS:
        try:
            encoding = 'utf-32' if content.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')) else 'utf-16' if content.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
            text = content.decode(encoding)
        except UnicodeDecodeError:
            raise ExtractionError('文件不是有效的UTF-8文本或带BOM的UTF-16/UTF-32文本，请转换编码后上传') from None
        if '\x00' in text or not text.strip():
            raise ExtractionError('文件必须包含有效文字，不能是二进制或空白内容')
        if len(text) > MAX_TEXT_CHARS:
            raise ExtractionError('提取正文超过100万字符，请拆分文件后上传')
        return text
    if extension in ('.docx', '.xlsx'):
        check_office_archive(content)
    if extension == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError('不支持加密PDF，请先解除密码保护')
        if len(reader.pages) > 200:
            raise ExtractionError('PDF最多支持200页，请拆分后上传')
        for number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ''
            if text.strip():
                add(f'[第{number}页]\n{text}')
    elif extension == '.docx':
        from docx import Document
        from docx.table import Table
        document = Document(BytesIO(content))
        for block in document.iter_inner_content():
            if isinstance(block, Table):
                for row in block.rows:
                    add('\t'.join(cell.text for cell in row.cells))
            else:
                add(block.text)
    elif extension == '.xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets) > 50:
                raise ExtractionError('Excel最多支持50个工作表，请拆分后上传')
            cells = 0
            for sheet in workbook.worksheets:
                rows, columns = sheet.max_row or 1, sheet.max_column or 1
                cells += rows * columns
                if rows > 10000 or columns > 200 or cells > 100000:
                    raise ExtractionError('Excel范围过大，最多支持10万单元格、每表1万行及200列，请缩小范围')
                lines = []
                for row in sheet.iter_rows(max_row=rows, max_col=columns, values_only=True):
                    if any(value is not None for value in row):
                        lines.append('\t'.join('' if value is None else str(value) for value in row))
                if lines:
                    add(f'[工作表：{sheet.title}]')
                    for line in lines:
                        add(line)
        finally:
            workbook.close()
    else:
        raise ExtractionError(UNSUPPORTED_MESSAGE)
    if not parts:
        raise ExtractionError('未提取到文字；扫描PDF或图片需要先进行OCR转换，空白文档无法建立索引')
    return '\n'.join(parts)
