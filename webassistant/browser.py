import asyncio
import time
from urllib.parse import urlparse

from .models import Candidate, SiteConfig
from .browsers import discover_browsers, launch_browser


class LoginFailure(Exception):
    pass


class CaptchaFailure(LoginFailure):
    pass


def find_chrome() -> str:
    # Compatibility helper for scripts/tests; detection now includes Edge and fallback.
    choices = discover_browsers()
    return choices[0].path if choices else ""


class SiteSession:
    def __init__(self, playwright, config: SiteConfig, progress=lambda _: None, browser=None):
        self.playwright = playwright
        self.config = config
        self.progress = progress
        self.browser = browser
        self.owns_browser = browser is None
        self.context = None
        self.page = None

    async def open(self, headless=False):
        self.config.validate()
        if self.browser is None:
            self.browser, self.browser_choice = await launch_browser(
                self.playwright, self.config.chrome_path, headless, self.progress)
        self.context = await self.browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN")
        self.context.set_default_timeout(self.config.timeout_ms)
        self.context.set_default_navigation_timeout(self.config.timeout_ms)
        self.page = await self.context.new_page()
        response = await self.page.goto(self.config.url, wait_until="domcontentloaded")
        self.check_origin()
        if response and response.status >= 400:
            raise LoginFailure(f"网站访问失败（HTTP {response.status}）")
        await self.page.locator("#districtGrid .district-card").first.wait_for()

    def check_origin(self):
        actual, expected = urlparse(self.page.url), urlparse(self.config.url)
        if (actual.scheme, actual.hostname, actual.port) != (expected.scheme, expected.hostname, expected.port):
            raise LoginFailure("页面跳转到了其他站点，已停止输入身份信息")

    async def choose_district(self, district: str):
        self.check_origin()
        card = self.page.get_by_role("button", name=f"进入{district}考试入口", exact=True)
        if await card.count() != 1:
            raise LoginFailure("网页中找不到唯一匹配的区县，请核对身份表中的区县名称")
        await card.click()
        await self.page.wait_for_load_state("domcontentloaded")
        await self.page.locator("#roleSwitch").wait_for()
        self.check_origin()
        # The static form is visible before login.js binds its role-switch handlers.
        # A rendered CAPTCHA proves the frontend initialization has completed.
        await self.captcha_bytes()
        await self.page.get_by_role("button", name="考生登录", exact=True).click()
        await self.page.locator('#roleSwitch [data-role="student"].active').wait_for()
        await self.page.get_by_placeholder("请输入姓名", exact=True).wait_for()
        await self.page.get_by_placeholder("请输入身份证号码", exact=True).wait_for()
        await self.captcha_bytes()

    async def fill_identity(self, candidate: Candidate):
        self.check_origin()
        await self.page.locator("#username").fill(candidate.name)
        await self.page.locator("#password").fill(candidate.id_number)

    async def captcha_bytes(self) -> bytes:
        self.check_origin()
        await self.page.wait_for_function("""() => {
            const i = document.querySelector('#captchaImage img#captcha');
            return (!!i && i.complete && i.naturalWidth > 0) || !!document.querySelector('.captcha-expired-box');
        }""")
        if await self.page.locator(".captcha-expired-box").count():
            await self.page.locator("#captchaImage").click()
        await self.page.wait_for_function("""() => {
            const i = document.querySelector('#captchaImage img#captcha');
            return !!i && i.complete && i.naturalWidth > 0;
        }""")
        return await self.page.locator("#captchaImage img#captcha").screenshot()

    async def refresh_captcha(self):
        old_src = await self.page.locator("#captcha").get_attribute("src") if await self.page.locator("#captcha").count() else ""
        await self.page.locator("#captchaImage").click()
        await self.page.wait_for_function("""old => {
            const i = document.querySelector('#captcha');
            return i && i.getAttribute('src') !== old && i.complete && i.naturalWidth > 0;
        }""", arg=old_src)

    async def submit(self, code: str, candidate: Candidate) -> tuple[str, str]:
        self.check_origin()
        before_src = await self.page.locator("#captcha").get_attribute("src")
        await self.page.locator("#captchaInput").fill(code)
        await self.page.locator("#loginButton").click()
        deadline = time.monotonic() + self.config.timeout_ms / 1000
        while time.monotonic() < deadline:
            if self.page.is_closed():
                raise LoginFailure("浏览器页面已关闭")
            self.check_origin()
            from playwright.async_api import Error as PlaywrightError
            try:
                # Read one DOM snapshot: separate count/get_attribute calls race with
                # redirects, and could mistake a newly shown identity error for a refresh.
                snapshot = await self.page.evaluate("""() => {
                    const notice = document.querySelector('#loginNotice');
                    const image = document.querySelector('#captcha');
                    const overlay = document.querySelector('#pageLoading');
                    const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
                    return {path: location.pathname,
                        notice: visible(notice) ? notice.textContent.trim() : '',
                        imageSrc: image ? image.getAttribute('src') : null,
                        imageReady: !!image && image.complete && image.naturalWidth > 0,
                        loading: visible(overlay)};
                }""")
            except PlaywrightError:
                if self.page.is_closed():
                    raise LoginFailure("浏览器页面已关闭")
                await asyncio.sleep(0.12)
                continue
            if snapshot['path'].rstrip("/") == "/exam/user/home":
                await self.page.get_by_text("个人资料", exact=True).first.wait_for()
                # A redirect alone is not sufficient evidence of the intended identity.
                await self.page.get_by_text(candidate.name, exact=False).first.wait_for()
                return "success", "登录成功，自动操作已结束，浏览器已保留"
            message = snapshot['notice']
            if message:
                if any(w in message for w in ("验证码", "登录凭证已失效")):
                    await self.captcha_bytes()
                    await self.page.wait_for_function("""old => {
                        const i = document.querySelector('#captcha');
                        return i && i.getAttribute('src') !== old && i.complete && i.naturalWidth > 0;
                    }""", arg=before_src)
                    return "captcha", message
                raise LoginFailure(message)
            # The frontend may silently renew an expired challenge, without an alert.
            if snapshot['imageReady'] and snapshot['imageSrc'] != before_src and not snapshot['loading']:
                return "captcha", "登录凭证或验证码已更新"
            await asyncio.sleep(0.12)
        raise LoginFailure("等待登录结果超时。请检查浏览器，本次不会自动重复提交")

    async def login(self, candidate: Candidate, ocr, manual_input=None):
        self.progress("正在打开浏览器和区县入口")
        await self.open()
        self.progress("正在选择区县并切换考生登录")
        await self.choose_district(candidate.district)
        await self.fill_identity(candidate)
        for attempt in range(1, 4):
            self.progress(f"正在本地识别验证码（{attempt}/3）")
            image = await self.captcha_bytes()
            code = await ocr.recognize(image)
            if not code:
                if attempt < 3:
                    await self.refresh_captcha()
                continue
            self.progress(f"正在提交登录（{attempt}/3）")
            state, message = await self.submit(code, candidate)
            if state == "success":
                return message
            self.progress(f"验证码未通过（{attempt}/3），等待新图片")
        if manual_input is None:
            raise CaptchaFailure("验证码连续三次识别或校验失败")
        while True:
            image = await self.captcha_bytes()
            self.progress("自动识别已尝试 3 次，请在软件内输入验证码，或点击停止")
            code = await self.wait_manual(manual_input, image)
            self.progress("正在提交人工输入的验证码")
            state, message = await self.submit(code, candidate)
            if state == "success":
                return message
            self.progress("验证码未通过，请重新输入当前图片")

    async def wait_manual(self, manual_input, image):
        if self.page.is_closed():
            raise LoginFailure("浏览器页面已关闭")
        manual_task = asyncio.create_task(manual_input(image))
        closed_task = asyncio.create_task(self.page.wait_for_event("close", timeout=0))
        try:
            done, _ = await asyncio.wait((manual_task, closed_task), return_when=asyncio.FIRST_COMPLETED)
            if closed_task in done or self.page.is_closed():
                raise LoginFailure("浏览器页面已关闭")
            return await manual_task
        finally:
            for task in (manual_task, closed_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(manual_task, closed_task, return_exceptions=True)

    async def close(self):
        try:
            if self.context:
                await self.context.close()
                self.context = None
        finally:
            if self.browser and self.owns_browser:
                await self.browser.close()
                self.browser = None


async def inspect_environment(playwright, config: SiteConfig, ocr, district: str, report):
    config.validate()
    checks = []

    def record(label, ok, detail):
        checks.append(ok)
        report(label, ok, detail)

    try:
        await ocr.check()
        record("本地 OCR", True, "识别模型加载正常")
    except Exception:
        record("本地 OCR", False, "模型或运行依赖加载失败，请检查软件文件是否完整")
    session = SiteSession(playwright, config)
    try:
        await session.open(headless=True)
        record("浏览器与网站", True, f"{session.browser_choice.name} 启动、网站访问和区县列表正常")
        if not district:
            district = (await session.page.locator(".district-name").first.inner_text()).strip()
        await session.choose_district(district)
        record("登录页面", True, "考生登录表单及验证码图片可用（未提交身份信息）")
    except Exception:
        record("浏览器与网页检查", False, "未通过；请检查浏览器路径、网络或页面结构")
    finally:
        await session.close()
    return all(checks)
