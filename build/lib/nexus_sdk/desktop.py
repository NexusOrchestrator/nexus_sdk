"""Windows desktop automation through Microsoft UI Automation or Win32 controls."""
import platform
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
    def __init__(self, app):
        self.native = app

    @classmethod
    def start(cls, executable, *, backend="uia", timeout=30):
        return cls(_application(backend).start(str(executable), timeout=timeout))

    @classmethod
    def connect(cls, *, process=None, title=None, title_re=None, backend="uia", timeout=30):
        criteria = {key: value for key, value in {"process": process, "title": title, "title_re": title_re}.items() if value is not None}
        if len(criteria) != 1:
            raise ConfigurationError("Informe exatamente um critério: process, title ou title_re.")
        return cls(_application(backend).connect(timeout=timeout, **criteria))

    def window(self, *, title=None, title_re=None, **criteria):
        options = {key: value for key, value in {"title": title, "title_re": title_re, **criteria}.items() if value is not None}
        return self.native.window(**options)

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
