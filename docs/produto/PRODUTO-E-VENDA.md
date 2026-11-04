# PRODUTO-E-VENDA.md — os dois produtos e como vendê-los

Este documento é para quem **vende** o Otto, não para quem o programa. Ele separa o que
existe em dois produtos, diz o que cada um faz e o que cada um não faz, mostra de onde vem
o custo de cada um e dá o roteiro de uma demonstração.

Para o produto por dentro, ver [`docs/produto/OTTO_PROJETO.md`](OTTO_PROJETO.md). Para o roteiro sem IA,
[`docs/guias/GUIA-FLUXO-SALAO.md`](../guias/GUIA-FLUXO-SALAO.md). Para a persona do agente,
[`docs/guias/GUIA-PERSONA-SALAO.md`](../guias/GUIA-PERSONA-SALAO.md).

---

## 1. Os dois produtos

Não são dois sistemas. São **dois cérebros dentro do mesmo sistema**, e a diferença que o
cliente compra é: o atendimento segue um roteiro que você escreveu, ou conversa livre.

| | **Otto Roteiro** (sem IA) | **Otto Agente** (com IA) |
|---|---|---|
| Como atende | menus numerados e perguntas fixas | conversa livre, com persona |
| O que o cliente final recebe | exatamente o que você escreveu | resposta gerada na hora |
| Pergunta fora do previsto | vai para a fila da equipe | tenta responder, e encaminha se travar |
| Custo por conversa | **zero** | por token do provedor |
| Precisa de chave de IA | **não** | sim (uma conta no provedor) |
| Consulta a agenda e diz o preço da data | sim | sim |
| Segura a data (pré-reserva) | sim | sim |
| Manda as fotos do espaço | sim | sim |
| Entende áudio do cliente | sim (transcrição) | sim (transcrição) |
| Responde pergunta que você não previu | não | sim |
| Lembra o cliente de conversas anteriores | não | sim |
| Sugere resposta para o atendente (copilot) | não | sim |
| Busca em base de conhecimento (RAG) | não | sim |

**A regra de bolso da venda:** quem tem um catálogo de perguntas repetidas (é o caso de
salão de festas: data, preço, capacidade, fotos, visita) é atendido muito bem pelo Roteiro.
Quem vende algo que muda a cada conversa, ou quer que o bot converse de verdade, quer o
Agente.

---

## 2. É o mesmo sistema — e isso é argumento de venda

Tudo o que não é o cérebro está nos **dois** produtos:

- Caixa de entrada única de WhatsApp, com vários atendentes e **captura de conversa**
  (quem puxa da fila é o dono; a conversa some da fila dos outros).
- Transferência entre atendentes, notas internas, busca por nome, telefone e mensagem.
- Tempo real: o que um atendente faz aparece na tela do outro.
- Mídia nos dois sentidos, com áudio do cliente virando texto na thread.
- Respostas rápidas com `/`, gestão de equipe por convite, relatórios.
- **Agenda com preço por data**, bloqueio, preenchimento em lote pelo calendário.
- **Pré-reserva atômica**: duas conversas disputando a mesma data — uma ganha, sempre.
- **Galeria do espaço** e **aviso de confirmação** no WhatsApp do cliente.
- Estado real de entrega em cada mensagem: enviado · entregue · lido · falhou.

**Trocar de produto é um clique**, em *Configurações › Agente de IA*: o controle tem três
estados — desligado · Roteiro · Agente. Sem migração, sem reinstalar, sem perder conversa.
Isso significa três coisas na venda:

1. **Vender o Roteiro não fecha a porta da IA.** O cliente sobe depois, no mesmo dia.
2. **A IA tem freio.** Se a fatura assustar ou o provedor cair, um clique devolve o
   atendimento ao Roteiro e o custo vai a zero — sem ficar sem atender.
3. **Conta nova nasce em Roteiro.** É o padrão do sistema: previsível e sem custo até
   alguém decidir o contrário.

---

## 3. O que nenhum dos dois faz — e você diz isso na venda

Prometer isto é criar um problema para o mês que vem:

- **Nenhum dos dois confirma reserva.** Os dois **seguram** a data por 48 h e avisam a
  equipe; quem confirma é uma pessoa, num clique no painel. É decisão de produto, não
  limitação: confirmar é compromisso financeiro.
- **Nenhum dos dois inventa preço.** Valor e disponibilidade saem sempre da agenda em tempo
  real. Data não cadastrada o sistema trata como desconhecida e **encaminha sem citar
  valor** — nunca estima. Se o dono não cadastrar as datas, o bot encaminha muito.
- **Nenhum dos dois lê imagem.** Foto, vídeo ou documento: o bot avisa que não consegue ver
  e, na segunda, chama a equipe. Quem olha a foto é gente — mas gente **é sempre chamada**.
- **O Roteiro não improvisa.** Pergunta fora do desenho vira fila da equipe. Isso é a
  qualidade dele, não o defeito: ele nunca inventa uma resposta errada.
- **O Agente pode errar.** Ele é um modelo de linguagem. As travas do sistema (preço só da
  agenda, nunca confirmar reserva, encaminhar quando trava) existem por isso, e é assim que
  se apresenta: um agente com coleira curta, não um oráculo.
- **Não há campanha em massa, disparo nem funil.** O Otto atende quem chega.

---

## 4. De onde vem o custo de cada um

| Custo | Roteiro | Agente |
|---|---|---|
| Servidor (API, banco, Redis, Evolution) | sim | sim |
| Número de WhatsApp | o do próprio cliente | o do próprio cliente |
| Mensagem enviada/recebida | **R$ 0** (conexão não-oficial, sem tarifa por mensagem) | **R$ 0** |
| Modelo de linguagem | **R$ 0** — nenhuma chamada | por token, a cada turno |
| Transcrição de áudio | por minuto de áudio (centavos) | por minuto de áudio (centavos) |
| Embeddings do RAG | não usa | por documento indexado, uma vez |

**A conta que decide a mensalidade do Agente.** Preencha com o preço atual do seu provedor
— ele muda, e por isso não está escrito aqui:

```
turnos/mês  =  conversas/mês  ×  turnos por conversa   (o sistema força handoff em 8)
custo/mês   =  turnos/mês  ×  (tokens de entrada + saída por turno)  ×  preço do provedor
```

Dois detalhes que mudam a conta e que valem citar:

- **Cada turno manda o conhecimento junto.** Persona, agenda e trechos do RAG entram no
  prompt a cada mensagem — o custo é dominado pela *entrada*, não pela resposta.
- **Existe um teto diário por cliente**, de 500 turnos de agente por dia
  (`AI_MAX_TURNS_DIA`). Ele não é comercial, é uma trava: sem ele, quem manda mensagem
  decide quanto o salão gasta. Estourado, o bot cala e a conversa fica para a equipe.

**A transcrição de áudio é a única chamada externa do Roteiro.** Custa frações de centavo
por nota de voz e existe porque boa parte do público brasileiro fala em vez de escrever —
recusar áudio no plano barato seria economizar no lugar errado. Ela usa a mesma chave do
provedor do agente quando ele sabe transcrever.

---

## 5. Estrutura de preço sugerida

Não é tabela; é a forma. Os números são seus.

**Implantação (uma vez).** É onde está o trabalho real: parear o WhatsApp, cadastrar a
agenda (datas e preços), subir as fotos, escrever o roteiro ou a persona com o dono, e
testar. Cobrar zero aqui é subsidiar a parte mais cara do relacionamento.

**Mensalidade, em dois níveis:**

- **Roteiro** — a base. Sem custo variável: o que você cobra é a plataforma e o suporte.
- **Agente** — a base **mais** o adicional de IA. O adicional precisa cobrir o custo por
  token com folga para meses de pico, porque o volume é do cliente, não seu.

**Por que dois níveis e não um só:** o mesmo salão que hoje não paga por IA já está dentro
do sistema, com agenda cadastrada e equipe treinada. Subir de nível é um clique e uma
conversa — é a venda mais barata que existe. Um preço único empurra o cliente pequeno para
fora antes de ele entrar.

**O que reajusta:** volume de conversas e número de atendentes. Não cobre por mensagem
enviada — o canal não cobra, e um preço por mensagem faz o cliente evitar de usar o
produto.

---

## 6. Como demonstrar (sem parear número nenhum)

O **simulador** existe para isto: *Configurações › Fluxo de atendimento*, abaixo do desenho
das caixas. Você digita o que um cliente mandaria e vê a conversa inteira — as bolhas, onde
ela parou, se foi para a fila e qual nota a equipe receberia.

O que é real na demonstração: a navegação, as variáveis e **a consulta à agenda**. O preço
que aparece é o preço cadastrado de verdade. O que não acontece: nada é enviado, nenhuma
data é segurada.

**Roteiro de 5 minutos, na ordem:**

1. Cadastre **uma data** com o preço real do cliente, na frente dele.
2. No simulador: `oi` → o menu. `1` → a pergunta de data → a data que você cadastrou.
   **Ele vê o preço dele saindo da agenda.**
3. Mande uma data **não cadastrada**. O bot encaminha **sem citar valor**. Esse é o momento
   da venda: mostre que o sistema prefere chamar gente a chutar preço.
4. `2` → as fotos. `3` → as dúvidas, e "cabe quantas pessoas?" escrito à mão.
5. Feche mostrando o controle de três estados: "isto aqui é o roteiro; este outro botão é a
   IA; e este primeiro desliga tudo se você quiser atender na mão hoje."

---

## 7. Riscos que se declaram na venda

Falar disto antes é o que evita a conversa ruim depois.

- **A conexão de WhatsApp é não-oficial** (Evolution/baileys, aparelho pareado). É o que
  torna o preço possível e o que traz o risco: a conta pode ser bloqueada pelo WhatsApp em
  uso abusivo. O Otto não dispara em massa justamente por isso.
- **Reconectar entrega o acumulado.** Ao voltar de uma queda, o WhatsApp despeja mensagens
  antigas de uma vez. O sistema tem guarda contra isso (não responde mensagem velha), mas o
  cliente vê conversas aparecendo em bloco.
- **Entrega depende do aparelho pareado estar no ar.** A Inbox mostra o estado real
  (enviado/entregue/lido/falhou), e falha de envio vira fila da equipe com nota — mas ela
  acontece.
- **O bot é o primeiro atendimento, não o último.** Todo caminho termina com uma pessoa
  quando o assunto sai do previsto. Vender "atendimento 100% automático" é vender outra
  coisa.

---

## 8. Onde cada promessa está implementada

Para conferir antes de prometer:

| Promessa | Onde |
|---|---|
| Preço sempre da agenda | `app/services/salao_agenda.py::resolver` |
| Duas conversas, uma data | índice `uq_salao_reservas_data_ativa` |
| O bot nunca confirma | `salao_agenda.confirmar`, só pela rota do painel |
| Roteiro não fala em dinheiro | validador do `sincronizar_conteudo.py` |
| Troca de cérebro num clique | `bot_settings.modo` |
| Teto de gasto diário | `AI_MAX_TURNS_DIA` |
| Estado real da entrega | `channels/whatsapp.py::_parse_status` |
