"""Windows desktop automation through Microsoft UI Automation or Win32 controls."""
import os
import platform
from pathlib import Path
from .errors import ConfigurationError


def _application(backend):
    if platform.system() != "Windows":
        raise ConfigurationError("Automação desktop está disponível apenas no Windows.")
    if backend not in {"uia", "win32"}:
        raise ConfigurationError("Backend deve ser uia ou win32.")
    try:
        from pywinauto.application import Application
    except ImportError as exc:
        raise ConfigurationError("Instale nexus-sdk[desktop] na VM Windows.") from exc
    return Application(backend=backend)


class DesktopApp:
    def __init__(self, app, *, capture_failure_screenshot=True, capture_mode="window"):
        if capture_mode not in {"window", "screen"}:
            raise ConfigurationError("capture_mode deve ser window ou screen.")
        self.native = app
        self.capture_failure_screenshot = capture_failure_screenshot
        self.capture_mode = capture_mode
        self._capture_window = None

    @classmethod
    def start(cls, executable, *, backend="uia", timeout=30, capture_failure_screenshot=True, capture_mode="window"):
        return cls(_application(backend).start(str(executable), timeout=timeout),
                   capture_failure_screenshot=capture_failure_screenshot, capture_mode=capture_mode)

    @classmethod
    def connect(cls, *, process=None, title=None, title_re=None, backend="uia", timeout=30,
                capture_failure_screenshot=True, capture_mode="window"):
        criteria = {key: value for key, value in {"process": process, "title": title, "title_re": title_re}.items() if value is not None}
        if len(criteria) != 1:
            raise ConfigurationError("Informe exatamente um critério: process, title ou title_re.")
        return cls(_application(backend).connect(timeout=timeout, **criteria),
                   capture_failure_screenshot=capture_failure_screenshot, capture_mode=capture_mode)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        if exc_type and self.capture_failure_screenshot:
            self._capture_failure()
        return False

    def window(self, *, title=None, title_re=None, **criteria):
        options = {key: value for key, value in {"title": title, "title_re": title_re, **criteria}.items() if value is not None}
        window = self.native.window(**options)
        self._capture_window = window
        return window

    def _capture_failure(self):
        artifacts = os.environ.get("NEXUS_ARTIFACTS_DIR")
        if not artifacts:
            return
        target = Path(artifacts) / f"desktop-failure-{self.capture_mode}.png"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if self.capture_mode == "screen":
                from PIL import ImageGrab
                ImageGrab.grab(all_screens=True).save(target)
                return
            window = self._capture_window or self.native.top_window()
            window.capture_as_image().save(target)
        except Exception:
            pass

    def control(self, window, *, title=None, auto_id=None, control_type=None, **criteria):
        options = {key: value for key, value in {"title": title, "auto_id": auto_id, "control_type": control_type, **criteria}.items() if value is not None}
        return window.child_window(**options)

    @staticmethod
    def click(control, *, timeout=30):
        control.wait("visible enabled", timeout=timeout)
        control.click_input()

    @staticmethod
    def fill(control, text, *, timeout=30):
        control.wait("visible enabled", timeout=timeout)
        control.set_edit_text(str(text))

    @staticmethod
    def text(control):
        return control.window_text()
