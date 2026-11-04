# Melhorias após a consolidação

## Avaliação após atendimento

Ainda não implementada. A pesquisa precisa identificar a conversa encerrada e
separar avaliação de nova demanda: hoje uma mensagem após encerrar abre outra conversa.
Antes de implementar, definir disparo explícito/automático, nota e comentário,
validade, resposta única, atribuição ao atendimento e métricas por conta. Não enviar
pesquisas retroativamente nem interpretar qualquer número como nota.

A base de encerramento foi corrigida nesta consolidação: posse, transições
condicionais, repetição sem evento duplicado, bloqueio de liberação/transferência
após resolver e distinção entre inatividade e resolução comprovada pelo bot.

## Operação e privacidade

- Política de retenção para conversas, anexos e payload bruto do canal é separada dos
  180 dias da auditoria. Não há descarte novo desses dados nesta entrega.
- Encerrar sessões WebSocket já abertas quando houver revogação de sessão.
- Armazenamento externo imutável para auditoria é uma evolução opcional; esta versão
  protege contra edição pela interface/API, não contra o administrador do banco.
- Resultados pendentes de operações externas precisam de reconciliação humana;
  uma requisição que falhou pode ter sido aceita pelo provedor.
