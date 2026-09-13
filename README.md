# Nexus SDK

SDK Python para criar, testar e empacotar automações que rodam no Nexus Agent.

Ele oferece uma forma padronizada de escrever bots com entrada tipada, logs estruturados, acesso a credenciais, execução local e empacotamento versionado para publicação no painel.

## Instalação

Para usar sempre a versão mais recente publicada no GitHub:

```bash
python -m pip install git+https://github.com/NexusOrchestrator/nexus_sdk.git
```

Para fixar uma versão específica:

```bash
python -m pip install git+https://github.com/NexusOrchestrator/nexus_sdk.git@v0.4.2
```

Para desenvolvimento local dentro do monorepo:

```bash
python -m pip install -e ./apps/sdk
```

## Criando um bot

```bash
nexus init meu-bot
cd meu-bot
nexus venv
```

O comando cria a estrutura inicial:

```text
meu-bot/
├── bot.py
├── inputs.json
├── nexus.toml
└── requirements.txt
```

O `requirements.txt` gerado fixa o SDK por tag pública do GitHub. Isso garante que o Agent instale a mesma versão do SDK quando executar o pacote em produção.

## Ambiente virtual

Crie o ambiente do projeto:

```bash
nexus venv
```

Por padrão, ele instala apenas o próprio SDK. Para instalar também as dependências do bot:

```bash
nexus venv --install-requirements
```

Para abrir uma sessão já com o ambiente ativado:

```bash
nexus venv --shell
```

Ou ative manualmente:

macOS/Linux:

```bash
source .venv/bin/activate
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
```

## Exemplo de bot

```python
from dataclasses import dataclass

from nexus_sdk import Automation, Context, Model, robot


@dataclass
class Entrada(Model):
    nome: str


@robot
class MeuBot(Automation):
    input_model = Entrada

    def run(self, ctx: Context, data: Entrada):
        ctx.log(f"Olá, {data.nome}")
        return {"mensagem": f"Processado para {data.nome}"}
```

Execute localmente:

```bash
nexus run --inputs inputs.json
```

## Validação

```bash
nexus validate
nexus validate --strict
```

O modo `--strict` é indicado antes de empacotar para produção.

## Empacotamento

```bash
nexus package --version 1.0.0
```

O pacote será gerado em:

```text
dist/meu-bot-1.0.0.zip
```

A versão também é atualizada no `nexus.toml`.

## Arquivo `nexus.toml`

Exemplo:

```toml
name = "meu-bot"
entrypoint = "bot.py"
version = "1.0.0"
credentials = []

[runtime]
python = "3.12"
sdk_min = "0.4.0"
```

## Comandos disponíveis

```bash
nexus init
nexus venv
nexus validate
nexus run
nexus package
```

## Publicação no GitHub

O SDK é distribuído pelo repositório público:

```text
https://github.com/NexusOrchestrator/nexus_sdk
```

Para publicar uma nova versão:

```bash
cd apps/sdk
git status
git add .
git commit -m "Release SDK 0.4.2"
git tag v0.4.2
git push origin main
git push origin v0.4.2
```

Atualize a versão em:

```text
apps/sdk/pyproject.toml
apps/sdk/src/nexus_sdk/__init__.py
apps/sdk/src/nexus_sdk/cli.py
```

## Testes

A partir da raiz do monorepo:

```bash
PYTHONPATH=$PWD/apps/sdk/src python -m unittest discover apps/sdk/tests
```

## Documentação relacionada

- [Guia completo do SDK](../../docs/sdk.md)
- [Deploy Railway](../../docs/deploy-railway.md)
