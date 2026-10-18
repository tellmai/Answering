"""Download an optional, app-local official Chrome for Testing. No system install."""
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path
from downloads import download


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tools/chrome"


def main():
    with urllib.request.urlopen("https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json", timeout=30) as response:
        stable = json.load(response)["channels"]["Stable"]
    asset = next(a for a in stable["downloads"]["chrome"] if a["platform"] == "win64")
    if not asset["url"].startswith("https://storage.googleapis.com/chrome-for-testing-public/"):
        raise ValueError("Unexpected Chrome download origin")
    DEST.mkdir(parents=True, exist_ok=True)
    archive = ROOT / "build/chrome-win64.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    print("Downloading official Chrome", stable["version"], flush=True)
    download(asset["url"], archive)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            target = (DEST / name).resolve()
            if not target.is_relative_to(DEST.resolve()):
                raise ValueError("Unsafe archive path")
        z.extractall(DEST)
    (DEST / "source.json").write_text(json.dumps({"version": stable["version"], "url": asset["url"]}, indent=2), encoding="utf-8")
    print("Chrome ready:", DEST / "chrome-win64/chrome.exe", flush=True)


if __name__ == "__main__":
    main()
