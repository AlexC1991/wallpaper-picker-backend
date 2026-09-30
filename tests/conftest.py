import json
from pathlib import Path

import pytest


def write_project(folder: Path, data: dict | str, bom: bool = False, extra: dict | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data)
    raw = text.encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    (folder / "project.json").write_bytes(raw)
    for name, content in (extra or {}).items():
        p = folder / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content if isinstance(content, bytes) else content.encode())
    return folder


@pytest.fixture
def xdg(tmp_path, monkeypatch):
    for var, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                     ("XDG_STATE_HOME", "state"), ("XDG_RUNTIME_DIR", "run")):
        monkeypatch.setenv(var, str(tmp_path / sub))
    return tmp_path
