import io
import json
from pathlib import Path
import socket
import shutil
import tarfile
from tempfile import TemporaryDirectory
import unittest
from urllib.request import ProxyHandler, build_opener

from pypdf import PdfReader

from http_fixtures import HTTPFixture


def fetch(url):
    with build_opener(ProxyHandler({})).open(url, timeout=3) as response:
        return response.read()


class HTTPFixtureTest(unittest.TestCase):
    def fixture(self, root, name, content, port=30180):
        task = root / name
        source = task / "files"
        source.mkdir(parents=True)
        (source / "data.json").write_text(json.dumps(content))
        session = root / f"session-{name}"
        session.mkdir()
        fixture = HTTPFixture(task, session, {"port": port, "source": "files"})
        self.addCleanup(fixture.close)
        fixture.start()
        return fixture

    def test_overlapping_tasks_using_same_legacy_port_stay_independent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self.fixture(root, "a", {"owner": "a", "link": "http://localhost:30180/data.json"})
            b = self.fixture(root, "b", {"owner": "b", "link": "http://localhost:30180/data.json"})
            try:
                self.assertNotEqual(a.url, b.url)
                self.assertEqual(json.loads(fetch(a.url + "/data.json")), {"owner": "a", "link": a.url + "/data.json"})
                self.assertEqual(json.loads(fetch(b.url + "/data.json"))["owner"], "b")
                # A new episode of the same kind cannot replace A's data.
                c = self.fixture(root, "a-again", {"owner": "a-again"})
                try:
                    self.assertEqual(json.loads(fetch(a.url + "/data.json"))["owner"], "a")
                    b.close()
                    b.close()
                    self.assertEqual(json.loads(fetch(a.url + "/data.json"))["owner"], "a")
                    self.assertEqual(json.loads(fetch(c.url + "/data.json"))["owner"], "a-again")
                    with self.assertRaises(OSError):
                        fetch(b.url + "/data.json")
                finally:
                    c.close()
            finally:
                a.close()
                b.close()

    def test_existing_listener_on_legacy_port_is_untouched(self):
        with TemporaryDirectory() as tmp, socket.socket() as sentinel:
            sentinel.bind(("127.0.0.1", 0))
            sentinel.listen()
            port = sentinel.getsockname()[1]
            fixture = self.fixture(Path(tmp), "task", {"ok": True}, port=port)
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    accepted, _ = sentinel.accept()
                    accepted.close()
                self.assertEqual(json.loads(fetch(fixture.url + "/data.json")), {"ok": True})
            finally:
                fixture.close()

    def test_read_only_assets_and_original_urls_are_preserved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "task/files"
            source.mkdir(parents=True)
            content = b'{"url":"http://localhost:30180/data.json"}'
            path = source / "data.json"
            path.write_bytes(content)
            path.chmod(0o444)
            source.chmod(0o555)
            session = root / "session"
            session.mkdir()
            fixture = HTTPFixture(root / "task", session, {"port": 30180, "source": "files"})
            try:
                fixture.start()
                self.assertEqual(path.read_bytes(), content)
                self.assertIn(fixture.url, fetch(fixture.url + "/data.json").decode())
                self.assertEqual(fixture.rewrite({"url": ["http://localhost:30180/a", "http://127.0.0.1:5432", "http://example.com:30180", "http://localhost:301800"]}), {"url": [fixture.url + "/a", "http://127.0.0.1:5432", "http://example.com:30180", "http://localhost:301800"]})
            finally:
                fixture.close()
                source.chmod(0o755)
                path.chmod(0o644)

    def test_missing_or_empty_fixture_is_rejected(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            task.mkdir()
            session = root / "session"
            session.mkdir()
            with self.assertRaises(FileNotFoundError):
                HTTPFixture(task, session, {"port": 30180, "source": "missing.tar.gz"})
            (task / "empty").mkdir()
            other = root / "other"
            other.mkdir()
            with self.assertRaisesRegex(ValueError, "no data files"):
                HTTPFixture(task, other, {"port": 30180, "source": "empty"})

    def test_archive_cannot_escape_session_directory(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / "task"
            task.mkdir()
            archive = task / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                entry = tarfile.TarInfo("../../escaped")
                entry.size = 3
                output.addfile(entry, io.BytesIO(b"bad"))
            session = root / "session"
            session.mkdir()
            with self.assertRaisesRegex(ValueError, "Unsafe fixture archive"):
                HTTPFixture(task, session, {"port": 30180, "source": "bad.tar.gz"})
            self.assertFalse((root / "escaped").exists())

    def test_all_declared_task_fixtures_start_and_serve_real_data(self):
        tasks = Path(__file__).resolve().parents[1] / "tasks/finalpool"
        count = 0
        for config_path in sorted(tasks.glob("*/task_config.json")):
            spec = json.loads(config_path.read_text()).get("http_fixture")
            if spec is None:
                continue
            count += 1
            with self.subTest(task=config_path.parent.name), TemporaryDirectory() as tmp:
                fixture = HTTPFixture(config_path.parent, Path(tmp), spec)
                try:
                    fixture.start()
                    fixture.check_ready()
                finally:
                    fixture.close()
        self.assertEqual(count, 127)

    def test_fixture_urls_embedded_in_corpus_pdfs_are_updated(self):
        tasks = Path(__file__).resolve().parents[1] / "tasks/finalpool"
        rewritten = 0
        for config_path in sorted(tasks.glob("*/task_config.json")):
            spec = json.loads(config_path.read_text()).get("http_fixture")
            if spec is None:
                continue
            for document in (config_path.parent / "initial_workspace").glob("*.pdf"):
                before = "\n".join(page.extract_text() for page in PdfReader(document).pages)
                if f"localhost:{spec['port']}" not in before:
                    continue
                rewritten += 1
                with self.subTest(task=config_path.parent.name, document=document.name), TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    fixture = HTTPFixture(config_path.parent, root, spec)
                    try:
                        fixture.start()
                        inputs = root / "inputs"
                        inputs.mkdir()
                        copied = inputs / document.name
                        original = document.read_bytes()
                        shutil.copyfile(document, copied)
                        fixture.rewrite_tree(inputs)
                        after = "\n".join(page.extract_text() for page in PdfReader(copied).pages)
                        self.assertEqual(after, fixture.rewrite(before))
                        self.assertEqual(document.read_bytes(), original)
                    finally:
                        fixture.close()
        self.assertEqual(rewritten, 11)


if __name__ == "__main__":
    unittest.main()
