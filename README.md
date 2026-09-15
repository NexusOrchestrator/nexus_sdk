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
python -m pip install git+https://github.com/NexusOrchestrator/nexus_sdk.git@v0.4.5
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

Desenvolvimento local (sem conta):

```bash
nexus init meu-bot                              # cria a estrutura inicial do projeto
nexus venv                                       # cria o ambiente virtual (--install-requirements para libs extras)
nexus validate --strict                          # valida nexus.toml e o entrypoint antes de publicar
nexus run --inputs inputs.json                   # executa o bot localmente
nexus package --version 1.0.0                    # gera dist/meu-bot-1.0.0.zip
```

Autenticado (Personal Access Token), com paridade total ao painel web:

```bash
nexus login --api-url https://api.suaempresa.com          # autentica o CLI e salva o token localmente
nexus whoami                                                # mostra a conta e organização autenticadas
nexus logout                                                # remove as credenciais salvas
nexus environment-use STAGING                               # define o ambiente padrão (valida acesso antes de salvar)
nexus publish --version 1.0.1 --publish                     # empacota, envia e publica uma nova versão
nexus automation-create --name "Meu Bot"                    # cria a automação sem publicar nenhuma versão
nexus automation-list --environment DEVELOPMENT              # lista as automações do ambiente
nexus pull --automation-id <id> --version 1.0.1 --output ./meu-bot   # baixa e extrai uma versão publicada
nexus set-current --version 1.0.1 --environment DEVELOPMENT  # define a versão ativa de um ambiente
nexus promote --from DEVELOPMENT --to STAGING --version 1.0.1  # promove uma versão para o próximo ambiente
nexus credential-create --name "API Key" --data CHAVE=valor  # cria uma credencial no ambiente selecionado
nexus credential-list --environment DEVELOPMENT              # lista credenciais do ambiente
nexus credential-bind --credential-id <id> --credential-id <id2>  # associa credenciais à automação do projeto
nexus environment-set --set CHAVE=valor --set OUTRA=valor2   # define variáveis de ambiente (ENV) da automação
nexus environment-get                                        # mostra as variáveis de ambiente (ENV) da automação
nexus trigger-create --name "Diário" --type SCHEDULE --cron "0 9 * * *"  # cria um disparador de agendamento
nexus trigger-list                                           # lista disparadores da automação do projeto
nexus queue-create --name "fila-entrada"                     # cria uma fila no ambiente selecionado
nexus queue-bind --input-queue-id <id> --output-queue-id <id2>  # associa filas de entrada/saída à automação
nexus queue-send --queue-id <id> --data pedido=123           # publica uma mensagem em uma fila
nexus webhook-create --name "Notificar" --url https://exemplo.com/hook --trigger-on SUCCEEDED  # ação de saída
nexus webhook-list                                           # lista as ações de saída (webhooks) da automação
nexus execution-create --request-key "chave-1" --input cidade=SP  # dispara uma nova execução
nexus execution-list --limit 20 --status FAILED               # lista execuções do ambiente
nexus execution-logs --execution-id <id> --limit 100          # mostra os logs de uma execução
```

Por padrão a saída é impressa de forma legível (tabela para listas, incluindo respostas paginadas com `items`/`total`; `chave: valor` para objetos); use `--json` antes do subcomando (ex: `nexus --json trigger-list`) para obter a resposta bruta da API em JSON, útil em scripts/CI.

Use `nexus environment-use STAGING` para definir o ambiente padrão usado quando `--environment` não é informado nos demais comandos; o comando valida se seu token tem acesso ao ambiente (consultando `/environments`) antes de salvar a preferência localmente.

`nexus pull` aceita `--automation-id` diretamente (sem precisar de um `nexus.toml` local), útil quando você ainda não tem o projeto na máquina; use `--zip-only` para salvar apenas o `.zip` sem extrair, e `--force` para sobrescrever arquivos existentes ao extrair.

Veja exemplos completos de cada comando em [docs/sdk.md](../../docs/sdk.md).

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
git commit -m "Release SDK 0.4.5"
git tag v0.4.5
git push origin main
git push origin v0.4.5
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
