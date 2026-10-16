"""Episode-owned HTTP fixtures, independent of container UID and networking.

Task assets remain immutable. A fixture is copied into the session's scratch
directory and served on an OS-assigned loopback port held for its full lifetime.
Only URLs for that task's former fixture port are rewritten; database and other
localhost services are unaffected.
"""
from __future__ import annotations

import functools
import hashlib
import io
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import threading
from typing import Any
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener
import zipfile

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, TextStringObject


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Fixture path escapes its task directory: {relative!r}")
    return path


def make_writable(directory: Path) -> None:
    """Grant owner access only on a newly created, session-owned copy."""
    for path in [directory, *directory.rglob("*")]:
        mode = path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR
        if path.is_dir():
            mode |= stat.S_IXUSR
        path.chmod(mode)


def copy_task(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination)
    make_writable(destination)


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


class HTTPFixture:
    def __init__(self, task_dir: Path, session_dir: Path, spec: dict[str, Any]):
        port = spec["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError(f"Invalid fixture port: {port!r}")
        self.legacy_port = port
        self._pattern = re.compile(
            rf"http://(?:localhost|127\.0\.0\.1|\[::1\]):{port}(?!\d)",
            re.IGNORECASE,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url = ""
        source = _inside(task_dir, spec["source"])
        extracted = session_dir / "http"
        if source.is_dir():
            copy_task(source, extracted)
        else:
            extracted.mkdir()
            with tarfile.open(source, "r:gz") as archive:
                # Fixtures contain data, never links or device nodes. Validate
                # before extracting so even a malformed asset cannot escape.
                members = archive.getmembers()
                for member in members:
                    name = PurePosixPath(member.name)
                    if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                        raise ValueError(f"Unsafe fixture archive member: {member.name!r}")
                archive.extractall(extracted, members=members, filter="data")
            make_writable(extracted)
        self.directory = _inside(extracted, spec.get("root", "."))
        if not self.directory.is_dir():
            raise ValueError(f"Missing fixture document root: {self.directory}")
        files = sorted(
            p for p in self.directory.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(self.directory).parts)
            and p.suffix != ".log"
        )
        if not files:
            raise ValueError(f"Fixture has no data files: {self.directory}")
        self._probe_file = files[0]

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("Fixture has already been started")
        handler = functools.partial(_Handler, directory=str(self.directory))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        try:
            # Links inside HTML/JSON must point at this same episode too.
            self.rewrite_tree(self.directory)
            self._thread = threading.Thread(
                target=functools.partial(self._server.serve_forever, poll_interval=0.05),
                name=f"toolathlon-fixture-{self._server.server_port}",
                daemon=True,
            )
            self._thread.start()
            self.check_ready()
        except BaseException:
            self.close()
            raise

    def rewrite(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._pattern.sub(self.url, value)
        if isinstance(value, list):
            return [self.rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: self.rewrite(item) for key, item in value.items()}
        return value

    def rewrite_tree(self, directory: Path) -> None:
        """Update URLs in episode-owned text, PDF, and Office inputs."""
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".pdf":
                self._rewrite_pdf(path)
                continue
            if path.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                self._rewrite_office(path)
                continue
            data = path.read_bytes()
            if b"\0" in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            rewritten = self.rewrite(text)
            if rewritten != text:
                path.write_text(rewritten, encoding="utf-8")

    def _rewrite_pdf(self, path: Path) -> None:
        # Parse text-showing operands, including strings inside TJ arrays, so
        # compressed streams and PDF string escaping are handled correctly.
        changed = False

        def rewrite_operand(value):
            nonlocal changed
            if isinstance(value, TextStringObject):
                text = self.rewrite(str(value))
                changed |= text != value
                return TextStringObject(text)
            if isinstance(value, (list, ArrayObject)):
                return ArrayObject(rewrite_operand(item) for item in value)
            return value

        writer = PdfWriter(clone_from=PdfReader(io.BytesIO(path.read_bytes())))
        try:
            for page in writer.pages:
                contents = page.get_contents()
                if contents is not None:
                    contents.operations = [
                        (rewrite_operand(operands), operator)
                        for operands, operator in contents.operations
                    ]
                    page.replace_contents(contents)
                for ref in page.get("/Annots", []):
                    action = ref.get_object().get("/A")
                    if action is not None:
                        action = action.get_object()
                        if uri := action.get("/URI"):
                            text = self.rewrite(str(uri))
                            changed |= text != uri
                            action[NameObject("/URI")] = TextStringObject(text)
            if changed:
                output = io.BytesIO()
                writer.write(output)
                path.write_bytes(output.getvalue())
        finally:
            writer.close()

    def _rewrite_office(self, path: Path) -> None:
        changed = False
        output = io.BytesIO()
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(output, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename.endswith((".xml", ".rels")):
                    rewritten = self.rewrite(data.decode("utf-8")).encode("utf-8")
                    changed |= rewritten != data
                    data = rewritten
                target.writestr(info, data)
        if changed:
            path.write_bytes(output.getvalue())

    def check_ready(self) -> None:
        relative = self._probe_file.relative_to(self.directory).as_posix()
        # Ignore host HTTP_PROXY settings for this strictly local request.
        with build_opener(ProxyHandler({})).open(f"{self.url}/{quote(relative)}", timeout=5) as response:
            actual = response.read()
            if response.status != 200 or hashlib.sha256(actual).digest() != hashlib.sha256(self._probe_file.read_bytes()).digest():
                raise RuntimeError(f"Fixture readiness check failed: {relative}")

    def close(self) -> None:
        if self._server is None:
            return
        if self._thread is not None and self._thread.is_alive():
            self._server.shutdown()
            self._thread.join()
        self._server.server_close()
        self._thread = None
        self._server = None
