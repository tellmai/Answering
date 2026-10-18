import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import unquote, urlparse

from downloads import download


ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "build/dependency-resolution.json").read_text(encoding="utf-8"))
for item in report["install"]:
    name, expected_version = item["metadata"]["name"], item["metadata"]["version"]
    try:
        version(name)
        continue  # Keep already installed compatible packages; pip validates constraints later.
    except PackageNotFoundError:
        pass
    info = item["download_info"]
    url = info["url"]
    if urlparse(url).hostname != "files.pythonhosted.org":
        raise ValueError("Unexpected package origin")
    destination = ROOT / "build/wheelhouse" / unquote(Path(urlparse(url).path).name)
    download(url, destination, info["archive_info"]["hashes"]["sha256"])
