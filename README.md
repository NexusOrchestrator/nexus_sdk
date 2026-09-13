# Nexus SDK

Framework Python 0.4.0 para criar automações integradas ao Nexus através do agent.
Oferece ciclo de vida `setup/run/teardown`, etapas com logs automáticos, validação
de entradas, acesso a credenciais e execução local usando o mesmo runtime da VM.
Funções existentes com `@robot` continuam compatíveis.

```sh
python -m pip install -e ./apps/sdk
nexus init meu-bot
cd meu-bot
nexus run --inputs inputs.json
nexus package
```

O pacote será `dist/meu-bot.zip`, conforme `name` em `nexus.toml` ou o nome da pasta.
Veja o [guia completo do SDK](../../docs/sdk.md).

A versão 0.4.0 inclui modelos tipados, `nexus validate --strict`, testes locais com
`run_robot`, erros classificados, repetição controlada, consumo da mensagem
atribuída e publicação transacional na fila de saída. O cofre é acessado por
`ctx.credential()`. Veja o [exemplo de microautomação](../../examples/microautomacao/README.md).
