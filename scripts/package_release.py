import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def archive(destination, files, base):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            z.write(path, path.relative_to(base))
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
    with destination.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"file": destination.name, "bytes": destination.stat().st_size, "sha256": checksum}


def main():
    app = DIST / "WebLoginAssistant"
    if not (app / "WebLoginAssistant.exe").is_file():
        raise RuntimeError("Build the EXE first")
    portable = DIST / "网页自动登录助手-Windows-x64.zip"
    results = [archive(portable, sorted(p for p in app.rglob("*") if p.is_file()), DIST)]
    files = [ROOT / name for name in ("main.py", "requirements.in", "requirements.txt", "WebLoginAssistant.spec", "pytest.ini", ".gitignore", ".gitattributes", "README.md", "启动软件.cmd", "使用说明.md", "THIRD_PARTY_NOTICES.md", "验证记录.md") if (ROOT / name).is_file()]
    for folder in ("webassistant", "scripts", "tests", "docs"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    results.append(archive(DIST / "网页自动登录助手-源码.zip", sorted(files), ROOT))
    (DIST / "release-manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
