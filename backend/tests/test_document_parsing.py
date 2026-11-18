from io import BytesIO
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from backend.app.files.extract import ExtractionError, check_office_archive, extract_document
from backend.app.files.parsing import parse_uploaded_document


def sample_documents():
    from docx import Document
    from openpyxl import Workbook
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    doc = Document()
    doc.add_paragraph('Word paragraph')
    doc.add_table(rows=1, cols=2).cell(0, 0).text = 'Table cell'
    word = BytesIO()
    doc.save(word)
    book = Workbook()
    sheet = book.active
    sheet.title = 'Stock'
    sheet.append(['Product', 'Count'])
    sheet.append(['Sample', '=1+1'])
    excel = BytesIO()
    book.save(excel)
    book.close()
    pdf = PdfWriter()
    page = pdf.add_blank_page(width=300, height=300)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): pdf._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 200 Td (PDF sample) Tj ET')
    page[NameObject('/Contents')] = pdf._add_object(stream)
    document = BytesIO()
    pdf.write(document)
    return [('.docx', word.getvalue(), 'Word paragraph'), ('.xlsx', excel.getvalue(), '=1+1'), ('.pdf', document.getvalue(), 'PDF sample')]


class DocumentParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_documents_preserve_structure_and_bom_without_executing_content(self):
        samples = {'.md': '# 标题\n\n```python\nraise RuntimeError("do not execute")\n```\n',
                   '.csv': '名称,数量\n样例,2\n', '.json': '{"说明":"示例"}',
                   '.yaml': '说明: 示例\n', '.py': '# source\nraise RuntimeError("do not execute")\n'}
        for extension, text in samples.items():
            self.assertEqual(await parse_uploaded_document(Path('unused'), text.encode('utf-8-sig'), extension), text)
        self.assertEqual(extract_document(samples['.md'].encode('utf-16'), '.md'), samples['.md'])
        for data in [b'\x00binary', b'\xffinvalid', b'   ']:
            with self.assertRaises(ExtractionError):
                extract_document(data, '.md')
        with self.assertRaisesRegex(ExtractionError, '100万'):
            extract_document(b'x' * 1000001, '.md')

    @unittest.skipUnless(os.name == 'nt', 'Windows hot-reload event loop')
    async def test_selector_loop_can_parse_when_uvicorn_reload_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            extension, content, expected = sample_documents()[0]
            path = Path(directory) / ('sample' + extension)
            path.write_bytes(content)
            def parse_in_selector():
                loop = asyncio.SelectorEventLoop()
                try:
                    return loop.run_until_complete(parse_uploaded_document(path, content, extension))
                finally:
                    loop.close()
            self.assertIn(expected, await asyncio.to_thread(parse_in_selector))

    async def test_real_office_and_pdf_documents_parse_in_bounded_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            for extension, content, expected in sample_documents():
                with self.subTest(extension=extension):
                    path = Path(directory) / ('sample' + extension)
                    path.write_bytes(content)
                    text = await parse_uploaded_document(path, content, extension)
                    self.assertIn(expected, text)

    async def test_empty_scanned_and_encrypted_pdf_fail_with_clear_message(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            for encrypted in (False, True):
                pdf = PdfWriter()
                pdf.add_blank_page(width=100, height=100)
                if encrypted:
                    pdf.encrypt('test-only')
                data = BytesIO()
                pdf.write(data)
                path = Path(directory) / 'sample.pdf'
                path.write_bytes(data.getvalue())
                with self.assertRaisesRegex(ExtractionError, '加密|OCR'):
                    await parse_uploaded_document(path, data.getvalue(), '.pdf')

    def test_office_zip_bomb_rejected_before_parser(self):
        data = BytesIO()
        with ZipFile(data, 'w') as archive:
            for index in range(2001):
                archive.writestr(f'part-{index}', '')
        with self.assertRaisesRegex(ExtractionError, '解压'):
            check_office_archive(data.getvalue())
