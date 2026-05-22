from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pygls import uris

PROJECT_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.e2e


class LspClient:
    def __init__(self, cwd: Path) -> None:
        env = os.environ.copy()
        src = str(PROJECT_ROOT / "src")
        env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "hyground"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._next_id = 0
        self._send_lock = threading.Lock()
        self._condition = threading.Condition()
        self._responses: dict[int, dict[str, Any]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._reader_error: BaseException | None = None
        self._stderr: queue.Queue[str] = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr_loop, daemon=True)
        self._stderr_reader.start()

    def initialize(self, root: Path) -> dict[str, Any]:
        root_uri = uris.from_fs_path(str(root))
        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {},
                "workspaceFolders": [{"uri": root_uri, "name": root.name}],
            },
        )
        self.notify("initialized", {})
        return result

    def did_open(self, uri: str, text: str, version: int = 1) -> None:
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "hy",
                    "version": version,
                    "text": text,
                }
            },
        )

    def request(self, method: str, params: Any | None = None, timeout: float = 10.0) -> Any:
        self._next_id += 1
        msg_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + timeout
        with self._condition:
            while msg_id not in self._responses:
                if self._reader_error is not None:
                    raise AssertionError(f"LSP reader failed: {self._reader_error!r}\n{self.stderr_text()}")
                if self.proc.poll() is not None:
                    raise AssertionError(f"LSP server exited with {self.proc.returncode}\n{self.stderr_text()}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"Timed out waiting for {method}\n{self.stderr_text()}")
                self._condition.wait(remaining)
            response = self._responses.pop(msg_id)
        if "error" in response:
            raise AssertionError(f"LSP error for {method}: {response['error']}\n{self.stderr_text()}")
        return response.get("result")

    def notify(self, method: str, params: Any | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def wait_notification(self, method: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                for notification in self._notifications:
                    if notification.get("method") == method:
                        return notification
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"Timed out waiting for notification {method}\n{self.stderr_text()}")
                self._condition.wait(remaining)

    def close(self) -> None:
        if self.proc.poll() is None:
            try:
                self.request("shutdown", timeout=5.0)
                self.notify("exit")
            except Exception:
                self.proc.terminate()
        try:
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5.0)

    def stderr_text(self) -> str:
        lines: list[str] = []
        while True:
            try:
                lines.append(self._stderr.get_nowait())
            except queue.Empty:
                break
        return "".join(lines)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        with self._send_lock:
            self.proc.stdin.write(header + payload)
            self.proc.stdin.flush()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    decoded = line.decode("ascii").strip()
                    if not decoded:
                        break
                    key, value = decoded.split(":", 1)
                    headers[key.lower()] = value.strip()
                length = int(headers["content-length"])
                body = self.proc.stdout.read(length)
                message = json.loads(body.decode("utf-8"))
                with self._condition:
                    if "id" in message:
                        self._responses[message["id"]] = message
                    else:
                        self._notifications.append(message)
                    self._condition.notify_all()
        except BaseException as exc:  # pragma: no cover - failure path reported by request().
            self._reader_error = exc
            with self._condition:
                self._condition.notify_all()

    def _read_stderr_loop(self) -> None:
        assert self.proc.stderr is not None
        for line in iter(self.proc.stderr.readline, b""):
            self._stderr.put(line.decode("utf-8", errors="replace"))


def position_of(source: str, needle: str, offset: int = 0) -> dict[str, int]:
    absolute = source.index(needle) + offset
    line = source.count("\n", 0, absolute)
    line_start = source.rfind("\n", 0, absolute) + 1
    return {"line": line, "character": absolute - line_start}


def text_document_position(uri: str, source: str, needle: str, offset: int = 0) -> dict[str, Any]:
    return {"textDocument": {"uri": uri}, "position": position_of(source, needle, offset)}


def completion_labels(result: dict[str, Any]) -> set[str]:
    return {item["label"] for item in result["items"]}


def test_lsp_end_to_end_features(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'e2e'\n")
    (tmp_path / "local_lib.py").write_text(
        'def make_thing(value):\n    """Make thing docs from Python."""\n    return f"thing-{value}"\n'
    )
    (tmp_path / "lib.hy").write_text('(defn helper []\n  "Helper docs from project Hy"\n  1)\n')
    main = tmp_path / "main.hy"
    source = """(import pathlib [Path])
(import json)
(import math)
(import cmath)
(import local-lib)
(import os.path [ex])

(defn foo [x]
  "Foo docs from e2e"
  (+ x 1))

(setv bar 41)
(lfor x (range 3) x)
(Path ".")
json.du
(math.sqrt 4)
(cmath.exp 1j)
(local-lib.make-thing bar)
(helper)
(foo bar)
"""
    main.write_text(source)
    uri = uris.from_fs_path(str(main))

    client = LspClient(tmp_path)
    try:
        initialize = client.initialize(tmp_path)
        commands = initialize["capabilities"]["executeCommandProvider"]["commands"]
        assert "hyground.reindexWorkspace" in commands

        client.did_open(uri, source)
        diagnostics = client.wait_notification("textDocument/publishDiagnostics")
        assert diagnostics["params"]["uri"] == uri

        lfor_completion = client.request(
            "textDocument/completion",
            text_document_position(uri, source, "lfor", 2),
        )
        assert "lfor" in completion_labels(lfor_completion)

        json_completion = client.request(
            "textDocument/completion",
            text_document_position(uri, source, "json.du", len("json.du")),
        )
        assert "json.dumps" in completion_labels(json_completion)

        import_member_completion = client.request(
            "textDocument/completion",
            text_document_position(uri, source, "[ex", len("[ex")),
        )
        assert "exists" in completion_labels(import_member_completion)

        lfor_hover = client.request(
            "textDocument/hover",
            text_document_position(uri, source, "lfor", 1),
        )
        assert "List comprehension" in lfor_hover["contents"]["value"]

        python_hover = client.request(
            "textDocument/hover",
            text_document_position(uri, source, "local-lib.make-thing", len("local-lib.make")),
        )
        assert "Make thing docs from Python" in python_hover["contents"]["value"]

        python_definition = client.request(
            "textDocument/definition",
            text_document_position(uri, source, "local-lib.make-thing", len("local-lib.make")),
        )
        assert python_definition[0]["uri"].endswith("local_lib.py")
        assert python_definition[0]["range"]["start"]["line"] == 0

        math_definition = client.request(
            "textDocument/definition",
            text_document_position(uri, source, "math.sqrt", len("math.s")),
        )
        assert math_definition[0]["uri"].endswith("math.pyi")
        assert math_definition[0]["range"]["start"]["line"] > 0

        cmath_definition = client.request(
            "textDocument/definition",
            text_document_position(uri, source, "cmath.exp", len("cmath.e")),
        )
        assert cmath_definition[0]["uri"].endswith("cmath.pyi")

        hy_definition = client.request(
            "textDocument/definition",
            text_document_position(uri, source, "helper", 2),
        )
        assert hy_definition[0]["uri"].endswith("lib.hy")

        document_symbols = client.request("textDocument/documentSymbol", {"textDocument": {"uri": uri}})
        assert {symbol["name"] for symbol in document_symbols} >= {"foo", "bar"}

        workspace_symbols = client.request("workspace/symbol", {"query": "helper"})
        assert any(
            symbol["name"] == "helper" and symbol["location"]["uri"].endswith("lib.hy")
            for symbol in workspace_symbols
        )

        references = client.request(
            "textDocument/references",
            {**text_document_position(uri, source, "foo", 1), "context": {"includeDeclaration": True}},
        )
        assert len(references) >= 2

        prepare_rename = client.request(
            "textDocument/prepareRename",
            text_document_position(uri, source, "foo", 1),
        )
        assert prepare_rename["start"] == position_of(source, "foo")

        rename = client.request(
            "textDocument/rename",
            {**text_document_position(uri, source, "foo", 1), "newName": "foo-renamed"},
        )
        rename_edits = rename["changes"][uri]
        assert len([edit for edit in rename_edits if edit["newText"] == "foo-renamed"]) >= 2

        signature = client.request(
            "textDocument/signatureHelp",
            text_document_position(uri, source, "foo bar", len("foo bar")),
        )
        assert signature["signatures"][0]["label"] == "(foo [x])"
    finally:
        client.close()


def test_lsp_reindex_command_refreshes_new_python_modules(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'e2e-reindex'\n")
    main = tmp_path / "main.hy"
    source = "(import fresh-lib)\nfresh-lib.hello\n"
    main.write_text(source)
    uri = uris.from_fs_path(str(main))

    client = LspClient(tmp_path)
    try:
        client.initialize(tmp_path)
        client.did_open(uri, source)
        client.wait_notification("textDocument/publishDiagnostics")

        before = client.request(
            "textDocument/completion",
            text_document_position(uri, source, "fresh-lib.he", len("fresh-lib.he")),
        )
        assert "fresh-lib.hello" not in completion_labels(before)

        (tmp_path / "fresh_lib.py").write_text(
            'def hello():\n    """Fresh docs after reindex."""\n    return 1\n'
        )
        result = client.request(
            "workspace/executeCommand",
            {"command": "hyground.reindexWorkspace", "arguments": [uri]},
        )
        assert result["ok"] is True

        after = client.request(
            "textDocument/completion",
            text_document_position(uri, source, "fresh-lib.he", len("fresh-lib.he")),
        )
        assert "fresh-lib.hello" in completion_labels(after)

        hover = client.request(
            "textDocument/hover",
            text_document_position(uri, source, "fresh-lib.hello", len("fresh-lib.he")),
        )
        assert "Fresh docs after reindex" in hover["contents"]["value"]

        definition = client.request(
            "textDocument/definition",
            text_document_position(uri, source, "fresh-lib.hello", len("fresh-lib.he")),
        )
        assert definition[0]["uri"].endswith("fresh_lib.py")
    finally:
        client.close()
