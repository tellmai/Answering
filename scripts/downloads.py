"""Resumable range downloads for large official build dependencies."""
import concurrent.futures
import hashlib
import urllib.request
from pathlib import Path


def download(url: str, destination: Path, expected_sha256=""):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and expected_sha256 and hashlib.sha256(destination.read_bytes()).hexdigest() == expected_sha256:
        return
    request = urllib.request.Request(url, method="HEAD")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                size = int(response.headers["Content-Length"])
            break
        except Exception:
            if attempt == 2:
                raise
    parts = destination.parent / (destination.name + ".parts")
    parts.mkdir(exist_ok=True)
    chunk_size = 2 * 1024 * 1024
    chunks = [(n, offset, min(offset + chunk_size, size) - 1) for n, offset in enumerate(range(0, size, chunk_size))]
    def fetch(item):
        n, start, end = item
        part = parts / str(n)
        if part.exists() and part.stat().st_size == end - start + 1:
            return part
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=30) as response, part.open("wb") as output:
                    if response.status != 206:
                        raise ValueError("Server does not support byte ranges")
                    while block := response.read(128 * 1024):
                        output.write(block)
                if part.stat().st_size != end - start + 1:
                    raise ValueError("Incomplete download")
                return part
            except Exception:
                if attempt == 2:
                    raise
    print(f"Downloading {destination.name} ({size // 1024 // 1024} MiB)", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, chunks))
    digest = hashlib.sha256()
    with destination.open("wb") as output:
        for n, _, _ in chunks:
            data = (parts / str(n)).read_bytes()
            digest.update(data)
            output.write(data)
    if expected_sha256 and digest.hexdigest() != expected_sha256:
        raise ValueError("SHA-256 mismatch: " + destination.name)
    print("Verified", destination.name, flush=True)
