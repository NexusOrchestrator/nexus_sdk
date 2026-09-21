"""SAP GUI for Windows Scripting facade. Does not automate SAP web/Fiori."""
import platform
from .errors import ConfigurationError, ValidationError


class SapGui:
    def __init__(self, session):
        self.native = session

    @classmethod
    def connect(cls, *, connection_index=0, session_index=0):
        if platform.system() != "Windows":
            raise ConfigurationError("SAP GUI Scripting requer uma VM Windows com SAP GUI instalado.")
        if connection_index < 0 or session_index < 0:
            raise ValidationError("Índices de conexão e sessão devem ser não negativos.")
        try:
            import win32com.client
        except ImportError as exc:
            raise ConfigurationError("Instale nexus-sdk[sap] na VM Windows.") from exc
        try:
            gui = win32com.client.GetObject("SAPGUI")
            engine = gui.GetScriptingEngine
            connection = engine.Children(connection_index)
            return cls(connection.Children(session_index))
        except Exception as exc:
            raise ConfigurationError("Não foi possível conectar ao SAP GUI. Abra uma sessão e confirme que o scripting está habilitado no cliente e no servidor.") from exc

    def find(self, element_id):
        return self.native.findById(element_id)

    def transaction(self, code):
        if not isinstance(code, str) or not code.strip():
            raise ValidationError("Informe o código da transação SAP.")
        self.native.StartTransaction(code.strip())

    def fill(self, element_id, value):
        self.find(element_id).text = str(value)

    def press(self, element_id):
        self.find(element_id).press()

    def send_key(self, key=0):
        self.find("wnd[0]").sendVKey(key)

    def status(self):
        bar = self.find("wnd[0]/sbar")
        return {"type": bar.MessageType, "text": bar.Text}
