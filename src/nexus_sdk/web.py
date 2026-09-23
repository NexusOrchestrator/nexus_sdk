"""Small synchronous Playwright facade; native page/locator objects remain available."""
from pathlib import Path
from .errors import ConfigurationError


class Browser:
    def __init__(self, *, browser="chromium", headless=True, timeout_ms=30000):
        if browser not in {"chromium", "firefox", "webkit"}:
            raise ConfigurationError("Navegador deve ser chromium, firefox ou webkit.")
        if timeout_ms <= 0:
            raise ConfigurationError("Timeout do navegador deve ser positivo.")
        self.browser_name, self.headless, self.timeout_ms = browser, headless, timeout_ms
        self._playwright = self._browser = self._context = None

    def __enter__(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigurationError("Instale nexus-sdk[web] para automatizar navegadores.") from exc
        self._playwright = sync_playwright().start()
        try:
            self._browser = getattr(self._playwright, self.browser_name).launch(headless=self.headless)
            self._context = self._browser.new_context(accept_downloads=True)
            self._context.set_default_timeout(self.timeout_ms)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, exc_type, *_):
        try:
            if exc_type and self._context:
                import os
                artifacts = os.environ.get("NEXUS_ARTIFACTS_DIR")
                if artifacts:
                    for index, page in enumerate(self._context.pages[-3:]):
                        try:
                            page.screenshot(path=str(Path(artifacts) / f"browser-failure-{index + 1}.png"), full_page=True)
                        except Exception:
                            pass
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._playwright:
                self._playwright.stop()
            self._context = self._browser = self._playwright = None

    def open(self, url: str):
        if not self._context:
            raise ConfigurationError("Use Browser dentro de um bloco with.")
        page = self._context.new_page()
        page.goto(url)
        return WebPage(page)


class WebPage:
    def __init__(self, page):
        self.native = page

    def locator(self, value: str, *, by="role", role="button"):
        if by == "role":
            return self.native.get_by_role(role, name=value)
        if by == "label":
            return self.native.get_by_label(value)
        if by == "text":
            return self.native.get_by_text(value)
        if by == "test_id":
            return self.native.get_by_test_id(value)
        if by == "css":
            return self.native.locator(value)
        raise ConfigurationError("Localizador deve ser role, label, text, test_id ou css.")

    def click(self, value: str, *, by="role", role="button"):
        self.locator(value, by=by, role=role).click()

    def fill(self, value: str, text: str, *, by="label", role="textbox"):
        self.locator(value, by=by, role=role).fill(text)

    def text(self, value: str, *, by="text", role="button") -> str:
        return self.locator(value, by=by, role=role).inner_text()

    def screenshot(self, path: str | Path, *, full_page=True) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.native.screenshot(path=str(target), full_page=full_page)
        return target

    def on_dialog(self, *, accept=True, prompt_text=None):
        """Handle subsequent JS alert/confirm/prompt dialogs; call before triggering them."""
        def handle(dialog):
            if accept:
                dialog.accept(prompt_text=prompt_text) if prompt_text is not None else dialog.accept()
            else:
                dialog.dismiss()
        self.native.on("dialog", handle)
        return handle

    def download(self, trigger, path: str | Path) -> Path:
        """Run a callback that starts a download and save its file."""
        with self.native.expect_download() as event:
            trigger()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        event.value.save_as(str(target))
        return target
