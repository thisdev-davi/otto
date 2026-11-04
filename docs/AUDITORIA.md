# Auditoria por conta

A trilha permite ao administrador consultar o que aconteceu na própria conta sem duplicar o conteúdo dos clientes. Ajuda a investigar incidentes e prestar contas; sua existência, sozinha, não comprova conformidade com a LGPD.

## O que é registrado

Cada registro contém conta, horário UTC, ator (`human`, `bot`, `system`, `anonymous` ou `signed_link`), usuário quando comprovado, ação, tipo/identificador do recurso, resultado, motivo estruturado e correlação. O endereço de rede é reduzido a `/24` em IPv4 e `/48` em IPv6. Usa-se o peer da conexão; cabeçalhos de proxy não são tratados como identidade do cliente.

Os triggers PostgreSQL registram cada alteração efetiva em todas as tabelas atuais com `tenant_id`, incluindo SQL condicional, ORM, tarefas e operações em lote. Os metadados contêm apenas nomes das colunas alteradas, nunca seus valores. Uma alteração sem diferença não produz outro evento. A gravação faz parte da transação: se a auditoria da alteração falhar, a alteração é revertida.

As rotas autenticadas registram acesso e resultado, incluindo leituras de conversas, perfis, agenda, conteúdo e configurações. A autenticação verifica usuário ativo, conta e versão da sessão. Recusas de administrador são associadas ao usuário comprovado. Credenciais sem identidade comprovada não são atribuídas a uma conta; senha incorreta de uma conta localizada é uma tentativa anônima, sem copiar o e-mail. Um link assinado identifica somente o recurso, não um atendente. WebSocket registra conexão e desconexão e verifica a sessão no handshake.

Cada turno do bot compartilha ator e correlação entre decisão, alterações e envio. Guardas registram motivos estruturados de descarte. Mensagens, prompts, respostas completas, senhas, chaves, tokens, URLs assinadas, cabeçalhos e snapshots de dados pessoais não entram na trilha. Metadados sem chave e formato permitidos são descartados.

## Efeitos externos

O adapter de canal persiste a intenção em transação independente antes de provisionar, remover ou enviar. Sem intenção persistida, não há despacho. O resultado é registrado depois: `accepted` significa aceito pelo provedor, não entregue ao destinatário. Falha de rede pode ser `unknown`; não há reenvio automático nesse adapter. Escritas em object storage seguem a mesma separação.

Uma falha ao persistir o resultado depois de um envio conhecido não transforma o envio em falha. O chamador recebe o resultado do provedor e a intenção pendente permanece para reconciliação. Não existe transação distribuída entre PostgreSQL, WhatsApp e object storage. A trilha de acesso HTTP é gravada em transação separada, com erro operacional se indisponível; ela não confirma alterações de negócio pendentes nem desfaz uma operação já confirmada.

## Consulta e exportação

Administradores acessam **Configurações → Auditoria**. As APIs exigem JWT e papel ADMIN consultado no banco; a conta sai da autenticação:

- `GET /audit`: `date_from`, `date_to` (ISO 8601 com fuso), `actor_id`, `action`, `resource_type`, `resource_id`, `result`, `cursor` e `limit` (50 por padrão, máximo 100).
- Resposta: `{items, next_cursor}`. A ordem é horário e UUID descendentes; o cursor impede repetição de linhas por eventos novos.
- `GET /audit/export`: os mesmos filtros, exceto paginação. Exporta CSV por streaming e neutraliza células interpretáveis como fórmulas.

Consultas e exportações também são auditadas. O início da exportação fica registrado antes dos bytes; `audit.export.completed` só aparece após a geração completa. Desconexão do cliente deixa o início sem conclusão, sem afirmar que o arquivo foi recebido.

## Retenção e operação

Os registros são imutáveis pelo produto. O PostgreSQL recusa UPDATE e DELETE; a única exceção é a rotina de retenção para linhas com mais de 180 dias. Ela executa na inicialização e de hora em hora, coordenada por advisory lock transacional entre instâncias. Conversas, anexos e reservas não são apagados por essa rotina. Não há preenchimento retrospectivo de eventos.

Administradores do banco ainda controlam funções, triggers e backups. A trilha não é uma cadeia criptográfica de integridade. Para novas tabelas com `tenant_id`, a migration precisa adicionar o trigger `otto_audit_changes`; os triggers atuais não são criados automaticamente para tabelas futuras. Exemplo:

```sql
CREATE TRIGGER otto_audit_changes AFTER INSERT OR UPDATE OR DELETE ON nova_tabela
FOR EACH ROW EXECUTE FUNCTION otto_audit_mutation();
```

A conta de execução precisa de permissões de INSERT/SELECT na trilha e da exclusão usada pela retenção; não exponha credenciais de banco aos usuários finais. Validação reproduzível: `tests/test_audit.py`, `tests/test_audited_provider.py` e `tests/test_ws_auth.py`, sempre com PostgreSQL/Redis exclusivos de testes.
