"""Motor de atendimento por fluxo: o roteiro determinístico, sem nenhuma chamada de LLM.

É o **segundo cérebro** do bot. `app/ai/llm.py::agent_turn` e `app/flow/engine.py::decidir`
produzem o MESMO dicionário de decisão (`reply`, `captured`, `status`, `summary`,
`priority`, `sentiment`), e `bot_orchestrator._handle` escolhe entre os dois por
`bot_settings.modo`. Tudo o que vem antes (as sete guardas) e depois (bolhas com cadência,
handoff atômico, nota de triagem, ações do salão, persistência) é o mesmo código.

Foi essa escolha que evitou tocar em `app/ai/llm.py`, o arquivo mais caro de manter
divergente da base (`PLATAFORMA.md` §5).
"""
