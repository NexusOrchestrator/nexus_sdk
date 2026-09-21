"""SMTP sending and read-only IMAP retrieval using the Python standard library."""
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
import imaplib
import smtplib
import ssl
from .errors import ConfigurationError, ValidationError


@dataclass(frozen=True)
class MailItem:
    uid: str
    sender: str
    subject: str
    text: str
    message: EmailMessage


def send_mail(*, host, port, username, password, sender, to, subject, text,
              html=None, attachments=(), starttls=True, ssl_enabled=False, timeout=30):
    if starttls == ssl_enabled:
        raise ConfigurationError("Escolha exatamente um modo seguro: STARTTLS ou SSL.")
    if not to or not text:
        raise ValidationError("Destinatário e mensagem são obrigatórios.")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    for path in attachments:
        target = Path(path)
        message.add_attachment(target.read_bytes(), maintype="application", subtype="octet-stream", filename=target.name)
    context = ssl.create_default_context()
    transport = smtplib.SMTP_SSL if ssl_enabled else smtplib.SMTP
    with transport(host, port, timeout=timeout, context=context) if ssl_enabled else transport(host, port, timeout=timeout) as client:
        if starttls:
            client.starttls(context=context)
        if username:
            client.login(username, password)
        client.send_message(message)


def read_mail(*, host, username, password, folder="INBOX", criteria="UNSEEN", limit=20, port=993):
    """Read newest matching messages without modifying seen/unseen state."""
    if not 1 <= limit <= 100:
        raise ValidationError("O limite deve estar entre 1 e 100 mensagens.")
    if not criteria or any(char in criteria for char in "\r\n"):
        raise ValidationError("Critério IMAP inválido.")
    messages = []
    with imaplib.IMAP4_SSL(host, port=port) as client:
        client.login(username, password)
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            raise ConfigurationError("Não foi possível abrir a pasta de e-mail.")
        status, data = client.uid("search", None, criteria)
        if status != "OK":
            raise ConfigurationError("Não foi possível pesquisar e-mails.")
        for uid in reversed(data[0].split()[-limit:]):
            status, parts = client.uid("fetch", uid, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = next((part[1] for part in parts if isinstance(part, tuple) and len(part) > 1), None)
            if not raw:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw)
            body = message.get_body(preferencelist=("plain",)) if message.is_multipart() else message
            text = body.get_content() if body else ""
            messages.append(MailItem(uid.decode(), str(message.get("From", "")), str(message.get("Subject", "")), text, message))
    return messages
