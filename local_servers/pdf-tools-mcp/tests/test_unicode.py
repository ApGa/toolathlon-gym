import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp.types import CallToolResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_tools_mcp import server


class PDFUnicodeTest(unittest.IsolatedAsyncioTestCase):
    async def call(self, name, arguments):
        content = await server.mcp.call_tool(name, arguments)
        if isinstance(content, tuple):
            content = content[0]
        # Exercise the transport serialization that previously killed the MCP
        # server, not just the tool's Python return value.
        result = CallToolResult(content=content)
        json.loads(result.model_dump_json())
        return "\n".join(item.text for item in content)

    async def test_extracted_text_preserves_unicode_and_repairs_surrogates(self):
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "日本語 😀 \ud83d\ude00 bad:\ud800/\udfff")])
        with TemporaryDirectory() as tmp, patch.object(server.PyPDF2, "PdfReader", return_value=reader):
            path = Path(tmp) / "example.pdf"
            path.touch()
            text = await self.call("read_pdf_pages", {"pdf_file_path": str(path), "start_page": 1, "end_page": 1})
        self.assertIn("日本語 😀 😀 bad:�/�", text)

    async def test_metadata_cannot_crash_serialization_and_next_call_succeeds(self):
        reader = SimpleNamespace(pages=[None], metadata={"/Title": "Title \ud800"})
        with TemporaryDirectory() as tmp, patch.object(server.PyPDF2, "PdfReader", return_value=reader):
            path = Path(tmp) / "example.pdf"
            path.touch()
            text = await self.call("get_pdf_info", {"pdf_file_path": str(path)})
            self.assertIn("Title �", text)
            reader.metadata = {"/Title": "Healthy follow-up"}
            text = await self.call("get_pdf_info", {"pdf_file_path": str(path)})
            self.assertIn("Healthy follow-up", text)

    async def test_error_response_also_repairs_invalid_input_text(self):
        text = await self.call("get_pdf_info", {"pdf_file_path": "/does-not-exist/\ud800.pdf"})
        self.assertIn("Error", text)
        self.assertNotIn("\ud800", text)
