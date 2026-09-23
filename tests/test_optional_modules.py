from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from nexus_sdk.errors import ConfigurationError, ValidationError
from nexus_sdk.excel import Spreadsheet
from nexus_sdk.web import WebPage, Browser
from nexus_sdk.desktop import DesktopApp
from nexus_sdk.sap import SapGui
from nexus_sdk.email import send_mail, read_mail
from nexus_sdk.cli import ensure_sdk_requirement, SDK_VERSION
from nexus_sdk.project import validate_project


def test_excel_create_append_update_and_reopen():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pedidos.xlsx"
        book = Spreadsheet.create(path, headers=["id", "status"])
        book.append({"id": 1, "status": "novo"})
        book.append({"id": 2, "status": "novo"})
        assert book.find("id", 2) == [{"id": 2, "status": "novo"}]
        book.update_row(3, {"status": "em andamento"})
        assert book.update_where("id", 1, {"status": "concluído"}) == 1
        assert book.update_where("id", 9, {"status": "concluído"}) == 0
        book.save()
        reopened = Spreadsheet.open(path)
        assert list(reopened.rows()) == [{"id": 1, "status": "concluído"}, {"id": 2, "status": "em andamento"}]
        reopened.close()


def test_excel_rejects_ambiguous_or_unknown_columns():
    book = Spreadsheet.create("unused.xlsx", headers=["id", "status"])
    book.append({"id": 1})
    book.append({"id": 1})
    with pytest.raises(ValidationError):
        book.update_where("id", 1, {"status": "ok"})
    assert book.update_where("id", 1, {"status": "ok"}, all_matches=True) == 2
    with pytest.raises(ValidationError):
        book.append({"unknown": "value"})
    book.close()


def test_web_uses_playwright_locators_and_keeps_native_page():
    native = MagicMock()
    page = WebPage(native)
    page.fill("Usuário", "teste")
    native.get_by_label.assert_called_once_with("Usuário")
    native.get_by_label.return_value.fill.assert_called_once_with("teste")
    page.click("Entrar")
    native.get_by_role.assert_called_once_with("button", name="Entrar")
    assert page.native is native
    with pytest.raises(ConfigurationError):
        page.locator("x", by="unknown")
    with pytest.raises(ConfigurationError):
        Browser().open("https://example.com")


def test_web_supports_xpath_locators():
    native = MagicMock()
    page = WebPage(native)
    xpath = '//*[@id="login"]'

    page.click(xpath, by="xpath")
    page.fill(xpath, "valor", by="xpath")
    assert page.text(xpath, by="xpath") == native.locator.return_value.inner_text.return_value

    assert native.locator.call_count == 3
    native.locator.assert_any_call(xpath)
    native.locator.return_value.click.assert_called_once_with()
    native.locator.return_value.fill.assert_called_once_with("valor")


def test_browser_lifecycle_closes_native_resources():
    playwright = MagicMock()
    manager = MagicMock()
    manager.start.return_value = playwright
    with patch("playwright.sync_api.sync_playwright", return_value=manager):
        with Browser() as browser:
            page = browser.open("https://example.com")
            assert page.native is playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value
        playwright.chromium.launch.return_value.new_context.return_value.close.assert_called_once()
        playwright.chromium.launch.return_value.close.assert_called_once()
        playwright.stop.assert_called_once()


def test_browser_failure_screenshot_can_be_disabled():
    playwright = MagicMock()
    manager = MagicMock()
    manager.start.return_value = playwright
    context = playwright.chromium.launch.return_value.new_context.return_value
    context.pages = []
    with patch("playwright.sync_api.sync_playwright", return_value=manager):
        with patch.dict("os.environ", {"NEXUS_ARTIFACTS_DIR": "/tmp/nexus-artifacts"}):
            with pytest.raises(RuntimeError):
                with Browser(capture_failure_screenshot=False):
                    raise RuntimeError("falha")
    context.close.assert_called_once()


def test_desktop_and_sap_require_windows():
    with patch("nexus_sdk.desktop.platform.system", return_value="Linux"):
        with pytest.raises(ConfigurationError):
            DesktopApp.connect(title="ERP")
    with patch("nexus_sdk.sap.platform.system", return_value="Linux"):
        with pytest.raises(ConfigurationError):
            SapGui.connect()


def test_desktop_capture_configuration_and_failure_artifact(tmp_path, monkeypatch):
    native = MagicMock()
    window = native.window.return_value
    image = window.capture_as_image.return_value
    monkeypatch.setenv("NEXUS_ARTIFACTS_DIR", str(tmp_path))
    app = DesktopApp(native, capture_mode="window")
    app.window(title="ERP")
    with pytest.raises(RuntimeError):
        with app:
            raise RuntimeError("falha")
    image.save.assert_called_once_with(tmp_path / "desktop-failure-window.png")

    with pytest.raises(ConfigurationError):
        DesktopApp(native, capture_mode="invalid")


def test_sap_uses_existing_session_without_opening_transaction_implicitly():
    session = MagicMock()
    sap = SapGui(session)
    sap.fill("wnd[0]/usr/txtFIELD", "123")
    assert session.findById.return_value.text == "123"
    sap.transaction("VA01")
    session.StartTransaction.assert_called_once_with("VA01")


def test_email_requires_tls_and_imap_is_read_only():
    with pytest.raises(ConfigurationError):
        send_mail(host="mail", port=25, username="a", password="b", sender="a@x.com", to="b@x.com", subject="Hi", text="hello", starttls=False)
    client = MagicMock()
    client.__enter__.return_value = client
    client.select.return_value = ("OK", [b""])
    client.uid.side_effect = [("OK", [b"12"]), ("OK", [(b"12", b"From: a@example.com\nSubject: Oi\n\nMensagem")])]
    with patch("nexus_sdk.email.imaplib.IMAP4_SSL", return_value=client):
        messages = read_mail(host="mail", username="u", password="p")
    client.select.assert_called_once_with("INBOX", readonly=True)
    assert messages[0].subject == "Oi"
    assert messages[0].text == "Mensagem"


def test_email_smtp_uses_starttls_before_login():
    client = MagicMock()
    client.__enter__.return_value = client
    with patch("nexus_sdk.email.smtplib.SMTP", return_value=client):
        send_mail(host="mail", port=587, username="u", password="p", sender="a@example.com", to="b@example.com", subject="Oi", text="Olá")
    assert client.starttls.call_count == 1
    client.login.assert_called_once_with("u", "p")
    assert client.send_message.call_count == 1


def test_sdk_extra_requirement_is_preserved_and_validated():
    with tempfile.TemporaryDirectory() as directory:
        project = Path(directory)
        (project / "bot.py").write_text("from nexus_sdk import robot\n@robot\ndef bot(ctx): return {}\n")
        (project / "nexus.toml").write_text('entrypoint = "bot.py"\n[runtime]\npython = "3.12"\n')
        requirement = f"nexus-sdk[web,excel] @ https://github.com/NexusOrchestrator/nexus_sdk/archive/refs/tags/v{SDK_VERSION}.zip\n"
        (project / "requirements.txt").write_text(requirement)
        assert not ensure_sdk_requirement(project)
        assert (project / "requirements.txt").read_text() == requirement
        assert not validate_project(project)["warnings"]
