#!/usr/bin/env python3
"""Check packaged files without importing the model or running any analysis."""

import ast
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile


def main():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "FILE_MANIFEST.json").read_text())
    expected = {r["path"]: r for r in manifest["files"]}
    withheld = set(manifest.get("withheld_local_files", []))
    actual = {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
              and ".git" not in p.relative_to(root).parts
              and "__pycache__" not in p.relative_to(root).parts}
    assert actual - withheld == set(expected) | {"FILE_MANIFEST.json"}, "Missing or unexpected public files"
    assert not (set(expected) & withheld), "Withheld file present in public manifest"
    for name, row in expected.items():
        path = root / name
        data = path.read_bytes()
        assert len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"], name
        if path.suffix == ".py":
            ast.parse(data, filename=name)
        if path.suffix == ".parquet":
            assert data[:4] == data[-4:] == b"PAR1", name
        if path.suffix == ".docx":
            with ZipFile(path) as archive:
                assert archive.testzip() is None
                assert "word/document.xml" in archive.namelist()
    for path in root.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" not in target and not target.startswith(("#", "mailto:")):
                assert (path.parent / target.split("#", 1)[0]).exists(), (path.name, target)
    print(f"PASS: {len(expected)} files; hashes, Python syntax, document containers and local links")
    print("Not executed: optimisation, model tests, statistical analysis, plot regeneration or dependency installation.")
    print("MIT covers original code only. Price-containing files are excluded; paper/PPT remain pending.")


if __name__ == "__main__":
    main()
