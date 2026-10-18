import asyncio
import re
from concurrent.futures import ThreadPoolExecutor


class LocalOCR:
    """One inference executor; the UI and browser loop never block on ONNX."""

    def __init__(self):
        self._engine = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-ocr")

    def _load(self):
        if self._engine is None:
            import ddddocr
            self._engine = ddddocr.DdddOcr(show_ad=False, use_gpu=False)
        return self._engine

    async def check(self):
        await asyncio.get_running_loop().run_in_executor(self._pool, self._load)

    async def recognize(self, image: bytes) -> str:
        def infer():
            return self._load().classification(image)
        result = await asyncio.get_running_loop().run_in_executor(self._pool, infer)
        result = re.sub(r"\s", "", str(result))
        return result if re.fullmatch(r"[a-zA-Z0-9]{1,10}", result) else ""

    def close(self):
        self._pool.shutdown(wait=False, cancel_futures=True)
