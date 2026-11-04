# Guia de conteúdo do Agente de IA

Como escrever o que o bot sabe e como ele age. Este guia é sobre **conteúdo**, não sobre
código. A implementação está em `app/ai/` e `app/services/bot_orchestrator.py`.

> **Onde o conteúdo mora neste repositório:** em `conteudo/`, versionado em git, e não na
> tela. `conteudo/persona.md` → Persona, `conteudo/conhecimento.md` → Conhecimento
> essencial, `conteudo/base/*.md` → Documentos. Publique com
> `python3 scripts/sincronizar_conteudo.py --conta "Seu Salão"`; a tela `/agente` passa a
> mostrar os dois primeiros em leitura. Tudo o que este guia ensina continua valendo — só
> muda o lugar onde se escreve, que agora tem diff, revisão e histórico.

Tudo aqui foi verificado contra o agente rodando de verdade (Gemini + pgvector), não
deduzido da documentação.

---

## As três camadas, e por que a divisão importa

| Campo | Quando vai ao modelo | Tamanho alvo | Serve para |
|---|---|---|---|
| **Persona** | **Sempre**, em toda mensagem | 1.500–3.500 caracteres | COMO atender: tom, fluxo e proibições |
| **Conhecimento essencial** | **Sempre**, em toda mensagem | 400–1.200 caracteres | Os poucos fatos que não podem faltar em resposta nenhuma |
| **Documentos** (base avançada) | Só os trechos que a pergunta puxar | Sem limite prático | O volume: catálogo, regras, FAQ |

A consequência prática dessa tabela é a única regra de ouro do guia:

> **Persona e Conhecimento essencial são pagos em toda mensagem trocada, o dia inteiro.
> Documentos são pagos só quando relevantes.**

Por isso conhecimento longo vai para Documentos, e por isso o Conhecimento essencial
precisa ser curto de verdade. O teste para decidir onde um fato mora:

> *"Isso precisa estar presente mesmo quando o cliente está falando de outra coisa?"*

Endereço, capacidade e horário: sim → Conhecimento essencial.
Política de cancelamento em 3 faixas: não → Documentos.

---

## 1. Persona

O que o bot **é** e como se comporta. É o bloco de maior peso: ele entra por último no
prompt, e modelos dão mais atenção ao que vem no fim.

### A regra que mais importa

**Toda proibição mora na Persona. Nunca apenas nos Documentos.**

A busca semântica pode não recuperar o trecho da regra — ela traz o que é *parecido* com
a pergunta, e "quanto custa?" não se parece com "não informamos preço". Uma trava que
depende da busca acertar é uma trava que depende de sorte.

Foi assim que o agente do Salão Exemplo passou no teste de preço: a proibição está na
Persona, não num documento.

### Estrutura recomendada

```
[Identidade]     Quem você é, de que empresa, o que faz. Uma frase.
                 Diga "assistente virtual" — ver abaixo.

COMO VOCÊ FALA
- idioma, tom, tamanho das mensagens
- uso de emoji (e onde NÃO usar)
- tratamento (você/senhor), uso do nome do cliente

O QUE VOCÊ NUNCA FAZ
- (as travas, uma por linha, cada uma com o porquê)

COMO VOCÊ CONDUZ
1..4  o fluxo da conversa, numerado

[Regras situacionais]  ex.: como tratar visita, reclamação, urgência

[Encaminhamento]  como falar (ou não falar) da transferência
```

### A identidade diz "assistente virtual"

Uma frase, com o nome e a empresa — e a palavra **assistente virtual**, não "atendente".
O cliente tem direito de saber com o que está falando, e o sistema já errou nisso: o prompt
genérico abria com *"você é uma atendente humana"* e a persona dizia *"atendente"*, então
nada na conversa revelava a IA.

O **como** — apresentar-se na primeira mensagem, uma vez só, e responder a verdade a "você
é um robô?" — já está no prompt genérico (`app/ai/llm.py::_agent_system`), porque vale para
todo tenant. Não repita na Persona: cada caractere ali é cobrado em toda mensagem.

### Escrevendo as proibições

Vago não funciona. Feche as escapatórias que o cliente vai tentar:

❌ `Não fale sobre preços.`

✅ `NUNCA informe preço, valor, taxa, desconto ou condição de pagamento. Nem faixa, nem
"a partir de", nem estimativa, mesmo que o cliente insista ou diga que já ouviu um valor.
Preço depende de data, número de convidados e formato — quem passa é o consultor humano.`

A segunda versão nomeia as variantes (faixa, estimativa, "a partir de"), antecipa a
pressão do cliente e **explica o motivo** — modelos seguem melhor uma regra que entendem.

Proibições que quase todo negócio precisa:

- preço / desconto / condição de pagamento;
- confirmar data, vaga, estoque ou disponibilidade (isso depende de sistema que o bot não vê);
- inventar qualquer coisa fora do conteúdo fornecido;
- pedir CPF, dados bancários ou documentos por mensagem;
- afirmar que é um robô ou uma IA, se você não quiser isso.

### Emoji

O sistema já orienta o uso por padrão: cerca de um por mensagem, junto da ideia
principal, e **nenhum** em cancelamento, reclamação, cobrança ou problema. Você não
precisa escrever isso na Persona — precisa escrever quando quiser **desviar** do padrão.

Vale desviar em dois casos:

- **Repertório do seu nicho.** "Combinam com festa — 🎉 🥳 🎂 ✨ para comemoração, 👋 na
  saudação, 📅 para data, 📍 para o endereço." Dar exemplos concretos rende mais que pedir
  "use emoji": o modelo escolhe melhor quando sabe o campo semântico do negócio.
- **Tom mais sóbrio.** Advocacia, saúde, cobrança: aí sim vale restringir explicitamente.

O erro a evitar é apertar demais sem perceber. Uma linha como *"emoji com parcimônia, no
máximo um por mensagem"* parece prudente e deixa o atendimento seco — foi exatamente o que
aconteceu na primeira versão da persona do Salão Exemplo.

Testar o caso sensível é obrigatório. Mande uma mensagem difícil ("preciso cancelar, minha
mãe faleceu") e confirme que **não vem emoji nenhum**. Um 😊 ali destrói a conversa.

### Regras situacionais valem ouro

O caso real desta base: perguntado "como agendo uma visita?", o bot pedia a preferência
do cliente **sem informar a janela de atendimento** — apesar de o horário estar nos
documentos e a busca ter recuperado o trecho. O modelo simplesmente não citava.

Uma linha na Persona resolveu:

> *Quando o assunto for conhecer o espaço, informe a janela de atendimento ANTES de
> perguntar a preferência do cliente — deixar a pessoa sugerir um horário impossível e
> depois recusar é um atrito que dá para evitar.*

Lição geral: **quando o conteúdo está certo e o comportamento está errado, o conserto é
na Persona, não no documento.**

---

## 2. Conhecimento essencial

Os fatos que sustentam qualquer resposta. Vai inteiro em toda mensagem, então é o campo
com a maior disciplina de tamanho.

### O que entra

- identidade e localização (nome, cidade, endereço);
- os 2 ou 3 números que definem o serviço (capacidade, prazo, área de cobertura);
- horário de funcionamento;
- **os limites que o bot precisa saber sempre** — ex.: "quem informa preço é o consultor";
- **o que o atendente humano precisa receber** — a lista de dados que o bot deve coletar.

### O que NÃO entra

Tabela de preços, catálogo, FAQ, políticas detalhadas, histórico da empresa. Isso é
Documento.

### Formato

Frases curtas e independentes, uma por linha, separadas por linha em branco. Sem
marcadores, sem títulos — é contexto para um modelo, não uma página para humano ler.

```
O Salão Exemplo é um espaço para festas e eventos localizado na Rua Exemplo, 100,
Centro.

Comporta até 150 convidados sentados e 220 em formato coquetel.

Visitas presenciais acontecem de segunda a sexta, das 10h às 16h, sempre com agendamento
prévio.

Valores variam por data, número de convidados e formato do evento — quem informa preço é
o consultor humano, nunca a Bia.

Para dar andamento a um orçamento, a equipe precisa de: nome completo, WhatsApp, tipo de
evento, data pretendida e número aproximado de convidados.
```

Repare nas duas últimas: uma repete a trava de preço que já está na Persona (redundância
proposital, é a regra mais cara de furar) e a outra diz ao bot **o que coletar**, que é o
que transforma a conversa em algo útil para o humano.

---

## 3. Documentos (base de conhecimento avançada)

O volume do que a empresa sabe. Cada documento é fatiado em pedaços, cada pedaço vira um
vetor, e a cada mensagem do cliente o sistema recupera **só os trechos parecidos com a
pergunta**.

### O erro nº 1: escrever para quem lê o documento inteiro

O texto é cortado em pedaços de ~1.200 caracteres. **A IA pode ver um pedaço sozinho**,
sem título, sem o parágrafo anterior, sem o resto do documento.

Então cada bloco precisa se sustentar sozinho:

❌ `Aqui a churrasqueira é de alvenaria e pode ser usada sem custo.`
  → num trecho isolado, "aqui" não quer dizer nada.

✅ `A churrasqueira do Salão Exemplo é de alvenaria, com grelha basculante e espeto
giratório. Fica na área externa, coberta, e pode ser usada sem custo adicional.`

**Repita o sujeito.** Escrever "O Salão Exemplo…" / "No Salão Exemplo…" dezenas de vezes parece
redundante lendo o documento inteiro — e é exatamente o que garante que o trecho sozinho
continue dizendo de quem se trata.

### Outras regras de escrita

- **Um assunto por parágrafo**, e o parágrafo começa dizendo qual é o assunto. O
  fatiamento tenta quebrar em fim de frase, então parágrafo curto raramente é partido no
  meio.
- **Nunca deixe uma resposta depender do parágrafo anterior.** Nada de "além disso",
  "como dito acima", "no caso anterior".
- **Números e condições por extenso, no mesmo bloco.** "Entre 90 e 30 dias, devolve
  metade" precisa estar junto do resto da política, não numa lista solta.
- **Evite tabelas e markdown pesado.** Vira ruído no vetor. Prosa curta funciona melhor.
- **FAQ em pergunta e resposta é o formato mais seguro**, porque cada par já nasce
  autocontido — e a pergunta do documento se parece com a pergunta do cliente, que é
  literalmente como a busca funciona.

### Como dividir em documentos

Divida por **assunto que o cliente pergunta**, não por seção de um contrato. Uma divisão
que funciona para a maioria dos negócios de serviço:

1. **Estrutura / catálogo** — o que você tem ou vende, com detalhes concretos.
2. **Regras e políticas** — o que pode e o que não pode.
3. **Processo comercial** — como contratar, agendar, cancelar, remarcar.
4. **Perguntas frequentes** — as dúvidas reais, no formato do cliente.

Documento de 2.000 a 4.000 caracteres é um bom tamanho: rende de 2 a 4 trechos, o
suficiente para a busca ter alternativas sem diluir o assunto.

### O que a busca não resolve

Existe um corte de relevância que descarta trechos distantes demais da pergunta. Ele
segura ruído óbvio ("como faço lasanha"), mas **não** segura pergunta sobre um serviço
vizinho que a empresa não presta — "vocês vendem passagem aérea?" cai dentro da faixa das
perguntas legítimas.

Quem recusa esse caso é a **instrução anti-alucinação** e, no fim, o **handoff**. Mais um
motivo para as travas viverem na Persona.

---

## Checklist antes de colocar em produção

**Persona**
- [ ] Diz quem é o bot, de que empresa e o que faz
- [ ] Tem um bloco explícito de proibições, cada uma com o motivo
- [ ] A trava de preço (ou equivalente do seu negócio) fecha as escapatórias
- [ ] Diz o que fazer quando não sabe: encaminhar, nunca inventar
- [ ] Descreve o fluxo passo a passo
- [ ] Tem regra situacional para os atritos conhecidos

**Conhecimento essencial**
- [ ] Abaixo de ~1.200 caracteres
- [ ] Só fatos que valem em qualquer conversa
- [ ] Lista os dados que o bot deve coletar para o humano
- [ ] Nenhuma tabela de preço, nenhum catálogo

**Documentos**
- [ ] Cada parágrafo se entende sozinho, sem o resto do documento
- [ ] O sujeito é repetido em vez de "aqui" / "o espaço" / "nós"
- [ ] Divididos por assunto que o cliente pergunta
- [ ] Existe um FAQ em pergunta e resposta
- [ ] Nada contraditório entre dois documentos

**Antes de ligar o atendimento automático**
- [ ] Provedor **não** é `stub` (o stub responde frases fixas e ignora persona e conhecimento)
- [ ] "Testar conexão" verde
- [ ] "Usar busca semântica" ligado, se você criou Documentos
- [ ] Nenhum documento com o selo **Reindexar**
- [ ] Você testou as perguntas do seu negócio — inclusive as que o bot **não** deve responder

---

## Como testar

Escreva de 5 a 10 perguntas reais **antes** de ligar o bot, misturando três tipos:

1. **Fato direto** — "tem estacionamento?" → deve responder com o dado exato.
2. **Comportamento** — "como agendo uma visita?" → deve seguir o fluxo da Persona.
3. **Trava** — "quanto custa no sábado?" → deve **recusar** e encaminhar.

O tipo 3 é o que mais gente esquece de testar, e é o que dá prejuízo: um bot que inventa
preço cria uma promessa que o negócio vai ter que honrar ou desmentir.

A tela `/agente` tem um testador de busca que mostra quais trechos uma pergunta recupera
— útil para separar "a busca não achou" de "achou e o modelo não usou". São problemas
diferentes: o primeiro se conserta no Documento, o segundo na Persona.

---

## Parâmetros do sistema (para calibrar expectativa)

Valores atuais do código, não configuráveis pelo painel:

| Parâmetro | Valor | Onde |
|---|---|---|
| Tamanho do trecho | 1.200 caracteres | `app/ai/chunking.py` |
| Sobreposição entre trechos | 200 caracteres | `app/ai/chunking.py` |
| Trechos recuperados por mensagem | 5 | `app/services/ai_context.py` |
| Corte de relevância (gemini / openai) | 0,50 / 0,55 | `app/ai/embeddings.py` |
| Histórico enviado ao modelo | últimas 24 mensagens | `app/services/ai_context.py` |
| Turnos do cliente antes do encaminhamento forçado | 8 | `app/services/bot_orchestrator.py` |
| Falhas seguidas da IA antes de desistir | 2 | `app/services/bot_orchestrator.py` |
| Respostas após o handoff, sem humano assumir | 10 | `app/services/bot_orchestrator.py` |
| Dimensão do vetor | 1.536 | `app/ai/embeddings.py` |

**Trocar o provedor de embeddings invalida a base.** Vetores de modelos diferentes não são
comparáveis, então a busca ignora documentos indexados com outro modelo — eles aparecem na
lista com o selo **Reindexar**. O conserto é rodar `scripts/sincronizar_conteudo.py` de novo:
ele re-ingere cada documento com o modelo atual.

---

## Exemplo completo

A base da **Salão Exemplo** (espaço de festas) está configurada neste sistema como
referência: Persona de 2.834 caracteres, Conhecimento essencial de 798 e quatro
Documentos somando ~11.500 caracteres em 14 trechos. Abra `/agente` para ler o conteúdo
inteiro e usar como molde.
