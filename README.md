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
python -m pip install git+https://github.com/NexusOrchestrator/nexus_sdk.git@v0.4.9
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
├── fixtures.json
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

### Credenciais e filas locais (`--fixtures`)

`nexus run` nunca contata a API nem o vault de credenciais do painel. Para testar um bot que usa `ctx.credential(...)` ou `ctx.queues`, crie um fixture com valores fictícios (não versionar). `nexus init` já gera um vazio:

```json
{
  "inputs": { "name": "Minha empresa" },
  "environment": {},
  "credentials": {},
  "queues": {}
}
```

Preencha só o que o bot usa. Exemplo completo, com fila de entrada (`input` com `message` aninhada) e de saída (`output`):

```json
{
  "inputs": { "nome": "Acme" },
  "environment": { "URL_BASE": "https://exemplo.com" },
  "credentials": { "erp": { "username": "teste", "password": "teste" } },
  "queues": {
    "input": {
      "id": "fila-1",
      "name": "entradas",
      "message": { "id": "msg-1", "payload": { "pedido_id": 123 }, "attempts": 1 }
    },
    "output": { "id": "fila-2", "name": "resultados" }
  }
}
```

```bash
nexus run --fixtures fixtures.json --publications-output publicacoes.json
```

Os nomes em `credentials` precisam bater com os usados em `ctx.credential(...)` e listados em `credentials = [...]` no `nexus.toml`. Sem `message`, `ctx.queues.consume()` sempre retorna `None`; sem `output`, `ctx.queues.publish(...)` lança `ConfigurationError`. `publish()` nunca escreve de volta no `fixtures.json` (só leitura/entrada) — `--publications-output` grava o que seria publicado em um arquivo JSON separado e descartável, sem enviar nada de verdade. As chaves de `environment` viram variáveis de ambiente reais (leia com `os.environ.get("CHAVE")`); um `.env` na raiz do projeto também é carregado automaticamente, com `environment` do fixture tendo prioridade em caso de chave repetida.

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
nexus doctor                                     # diagnostica ambiente local, projeto e autenticação
nexus bump --part minor                          # incrementa a versão em nexus.toml (major/minor/patch)
nexus run --inputs inputs.json                   # executa o bot localmente
nexus package --version 1.0.0                    # gera dist/meu-bot-1.0.0.zip
```

Autenticado (Personal Access Token), com paridade total ao painel web:

Cada `nexus login` salva um **perfil** separado (URL da API + token), nomeado a partir da URL (ou `--profile <nome>` para escolher o nome). Isso cobre tanto múltiplos ambientes (local/staging/produção) quanto múltiplas contas dentro do mesmo ambiente — basta nomear os perfis explicitamente:

```bash
nexus login --api-url http://localhost:8000 --profile local

nexus login --api-url https://api-staging.suaempresa.com --profile staging-clienteA
nexus login --api-url https://api-staging.suaempresa.com --profile staging-clienteB

nexus login --api-url https://api.suaempresa.com --profile prod-clienteA
nexus login --api-url https://api.suaempresa.com --profile prod-clienteB

nexus profile-list                                          # lista os perfis salvos e qual está ativo
nexus profile-use prod-clienteB                              # troca o perfil ativo sem pedir login de novo
```

Sem `--profile`, o nome é derivado da URL da API — então dois logins na mesma URL sem `--profile` explícito se sobrescrevem; use nomes explícitos quando tiver mais de uma conta na mesma URL.

Também dá para sobrepor o perfil ativo só na sessão atual do terminal, sem mexer no padrão salvo:

```bash
NEXUS_PROFILE=local nexus publish --version 1.0.1 --publish
```

```bash
nexus login --api-url https://api.suaempresa.com          # autentica o CLI e salva o token localmente
nexus whoami                                                # mostra a conta e organização autenticadas
nexus logout                                                # remove as credenciais salvas (use --profile para remover só um perfil)
nexus environment-use STAGING                               # define o ambiente padrão (valida acesso antes de salvar)
nexus publish --version 1.0.1 --publish                     # empacota, envia e publica uma nova versão
nexus publish --version 1.0.1 --environment DEVELOPMENT      # publica e já define como atual no ambiente (implica --publish)
nexus automation-create --name "Meu Bot"                    # cria a automação sem publicar nenhuma versão
nexus automation-list --environment DEVELOPMENT              # lista as automações do ambiente
nexus automation-get --automation-id <id>                    # ver detalhes de uma automação
nexus automation-update --automation-id <id> --name "Novo nome"  # atualiza a automação (substitui todos os campos)
nexus automation-archive --automation-id <id>                # arquiva a automação
nexus automation-restore --automation-id <id>                # restaura uma automação arquivada
nexus automation-delete --automation-id <id>                 # remove a automação
nexus pull --automation-id <id> --version 1.0.1 --output ./meu-bot   # baixa e extrai uma versão publicada
nexus set-current --version 1.0.1 --environment DEVELOPMENT  # define a versão ativa de um ambiente
nexus promote --from DEVELOPMENT --to STAGING --version 1.0.1  # promove uma versão para o próximo ambiente
nexus deployment-list                                         # mostra a versão implantada em cada ambiente
nexus deployment-history                                      # histórico de promoções/implantações
nexus deployment-rollback --event-id <id>                     # reverte uma promoção usando o histórico
nexus environment-list                                        # lista os ambientes disponíveis para o seu perfil
nexus credential-create --name "API Key" --data CHAVE=valor  # cria uma credencial no ambiente selecionado
nexus credential-update --credential-id <id> --name "API Key" --data CHAVE=novo_valor  # atualiza uma credencial
nexus credential-delete --credential-id <id>                 # remove uma credencial
nexus credential-list --environment DEVELOPMENT              # lista credenciais do ambiente
nexus credential-bind --credential-id <id> --credential-id <id2>  # associa credenciais à automação do projeto
nexus environment-set --set CHAVE=valor --set OUTRA=valor2   # define variáveis de ambiente (ENV) da automação
nexus environment-get                                        # mostra as variáveis de ambiente (ENV) da automação
nexus trigger-create --name "Diário" --type SCHEDULE --cron "0 9 * * *"  # cria um disparador de agendamento
nexus trigger-list                                           # lista disparadores da automação do projeto
nexus trigger-update --trigger-id <id> --name "Diário" --type SCHEDULE --cron "0 10 * * *"  # atualiza um disparador
nexus trigger-toggle --trigger-id <id>                       # ativa/pausa um disparador
nexus trigger-delete --trigger-id <id>                       # remove um disparador
nexus queue-create --name "fila-entrada"                     # cria uma fila no ambiente selecionado
nexus queue-get --queue-id <id>                              # ver detalhes de uma fila
nexus queue-update --queue-id <id> --name "fila-entrada-v2"  # atualiza nome/descrição de uma fila
nexus queue-messages --queue-id <id> --status FAILED         # lista mensagens de uma fila
nexus queue-delete --queue-id <id>                           # remove uma fila (sem vínculos/histórico)
nexus queue-bind --input-queue-id <id> --output-queue-id <id2>  # associa filas de entrada/saída à automação
nexus queue-send --queue-id <id> --data pedido=123           # publica uma mensagem em uma fila
nexus webhook-create --name "Notificar" --url https://exemplo.com/hook --trigger-on SUCCEEDED  # ação de saída
nexus webhook-list                                           # lista as ações de saída (webhooks) da automação
nexus webhook-update --webhook-id <id> --name "Notificar" --url https://exemplo.com/hook2  # atualiza um webhook
nexus webhook-delete --webhook-id <id>                       # remove um webhook
nexus execution-create --request-key "chave-1" --input cidade=SP  # dispara uma nova execução
nexus execution-list --limit 20 --status FAILED               # lista execuções do ambiente
nexus execution-logs --execution-id <id> --limit 100          # mostra os logs de uma execução
nexus execution-cancel --execution-id <id>                    # cancela uma execução em fila ou em andamento
nexus execution-retry --execution-id <id>                     # repete uma execução criando uma nova a partir dela
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
git commit -m "Release SDK 0.4.9"
git tag v0.4.9
git push origin main
git push origin v0.4.9
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
