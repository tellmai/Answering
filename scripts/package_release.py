import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
from webassistant import APP_NAME, __version__


def describe(path):
    with path.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": checksum}


def archive(destination, files, base):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in files:
            z.write(path, path.relative_to(base))
    with zipfile.ZipFile(destination) as z:
        if z.testzip() is not None:
            raise RuntimeError("Archive integrity check failed")
    return describe(destination)


def main():
    app = DIST / APP_NAME
    release_dir = DIST / "releases" / f"v{__version__}"
    release_dir.mkdir(parents=True, exist_ok=True)
    if not (app / f"{APP_NAME}.exe").is_file():
        raise RuntimeError("Build the EXE first")
    if not (app / "_internal/chrome/chrome-win64/chrome.exe").is_file():
        raise RuntimeError("The complete release must include fallback Chrome")
    if not (app / "验证记录.md").is_file():
        raise RuntimeError("Run EXE validation and write_validation.py first")
    portable = release_dir / f"{APP_NAME}-Windows-x64-v{__version__}.zip"
    results = [archive(portable, sorted(p for p in app.rglob("*") if p.is_file()), DIST)]
    files = [ROOT / name for name in ("main.py", "requirements.in", "requirements.txt", "Answering.spec", "pytest.ini", ".gitignore", ".gitattributes", "README.md", "启动软件.cmd", "使用说明.md", "THIRD_PARTY_NOTICES.md", "验证记录.md") if (ROOT / name).is_file()]
    for folder in ("webassistant", "scripts", "tests", "docs"):
        files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    results.append(archive(release_dir / f"{APP_NAME}-Source-v{__version__}.zip", sorted(files), ROOT))
    for source, name in ((app / f"{APP_NAME}.exe", f"{APP_NAME}.exe"),
                         (ROOT / "使用说明.md", "UserGuide.md"),
                         (ROOT / "验证记录.md", "Validation.md")):
        destination = release_dir / name
        shutil.copy2(source, destination)
        results.append(describe(destination))
    (release_dir / "release-manifest.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    sums = results + [describe(release_dir / "release-manifest.json")]
    (release_dir / "SHA256SUMS.txt").write_text("".join(f"{item['sha256']}  {item['file']}\n" for item in sums), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
