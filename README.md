# Nexus SDK

Framework Python 0.4.1 para criar automações integradas ao Nexus através do agent.
Oferece ciclo de vida `setup/run/teardown`, etapas com logs automáticos, validação
de entradas, acesso a credenciais e execução local usando o mesmo runtime da VM.
Funções existentes com `@robot` continuam compatíveis.

```sh
python -m pip install git+https://github.com/NexusOrchestrator/nexus_sdk.git@v0.4.1
nexus init meu-bot
cd meu-bot
nexus venv
nexus run --inputs inputs.json
nexus package --version 1.0.0
```

O pacote será `dist/meu-bot-1.0.0.zip`, conforme `name` e `version` em `nexus.toml`.
O projeto gerado já inclui o SDK fixado em `requirements.txt` pela tag pública do GitHub,
para que o Nexus Agent consiga instalar a mesma versão ao executar o pacote.
Veja o [guia completo do SDK](../../docs/sdk.md).

A versão 0.4.1 inclui modelos tipados, `nexus validate --strict`, testes locais com
`run_robot`, erros classificados, repetição controlada, consumo da mensagem
atribuída e publicação transacional na fila de saída. O cofre é acessado por
`ctx.credential()`. Veja o [exemplo de microautomação](../../examples/microautomacao/README.md).
