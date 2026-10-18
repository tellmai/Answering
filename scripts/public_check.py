"""Read-only browser verification; never fill identities or solve a challenge."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webassistant.browser import inspect_environment
from webassistant.models import SiteConfig
from webassistant.ocr import LocalOCR


async def main():
    from playwright.async_api import async_playwright
    results = []
    ocr = LocalOCR()
    try:
        async with async_playwright() as playwright:
            ok = await inspect_environment(playwright, SiteConfig(), ocr, "黔江区",
                lambda label, passed, detail: results.append({"check": label, "passed": passed, "detail": detail}))
    finally:
        ocr.close()
    destination = Path(__file__).resolve().parents[1] / "build/public-check.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"ok": ok, "checks": results, "identity_submitted": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
