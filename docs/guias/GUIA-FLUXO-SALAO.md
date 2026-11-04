# Atendimento por fluxo — o roteiro do salão

Este documento é o **conteúdo** do nível 1: o roteiro determinístico que atende no WhatsApp
sem nenhuma chamada de modelo de linguagem.

Para a arquitetura e as invariantes, ver `CLAUDE.md` §7. Para a persona do agente de IA, que
é o nível 3 e vive em paralelo, ver `docs/guias/GUIA-PERSONA-SALAO.md`. Aqui está só como ler, alterar e
publicar o roteiro.

**Onde ele mora: [`conteudo/fluxo.json`](../../conteudo/fluxo.json).** Mesmo caminho da persona,
mesmo comando:

```bash
python3 scripts/sincronizar_conteudo.py --check                  # valida sem tocar no banco
python3 scripts/sincronizar_conteudo.py --conta "Seu Salão" --dry-run
python3 scripts/sincronizar_conteudo.py --conta "Seu Salão"
```

Depois disso a tela *Configurações › Fluxo de atendimento* desenha as caixas conectadas, **em
leitura**, apontando para o arquivo. Para o roteiro entrar no ar, ainda falta escolher
**Roteiro (sem IA)** em *Configurações › Agente de IA*.

---

## Quando usar roteiro e quando usar agente

| | Roteiro (nível 1) | Agente de IA (nível 3) |
|---|---|---|
| Custo por conversa | zero | por token |
| Precisa de chave de provedor | não | sim |
| O que o cliente recebe | exatamente o que você escreveu | resposta gerada |
| Sai do assunto | vai para a fila da equipe | tenta responder |
| Consulta a agenda | sim, pelo nó `agenda` | sim, pelo bloco de conhecimento |
| Segura uma data | sim, pelo nó `acao` | sim, pelo `_acao_pre_reserva` |

Os dois usam **os mesmos serviços de agenda e galeria**. Trocar de um para o outro é um clique
no painel, sem migration e sem reiniciar nada — e é também o freio de emergência quando o
provedor de IA cai ou a fatura assusta.

---

## O que o roteiro atende

Seis portas no menu principal, e nenhuma delas é beco sem saída:

| Opção | Para onde vai | Termina em |
|---|---|---|
| 1. Ver datas e valores | pergunta a data → nó `agenda` → os seis ramos | pré-reserva, ou fila da equipe |
| 2. Ver fotos do espaço | anúncio + galeria pelo nó `acao` | menu com as outras portas |
| 3. Tirar dúvidas sobre o espaço | submenu com cinco respostas prontas | volta ao menu, nunca encerra |
| 4. Agendar uma visita | informa a janela de visitas | fila da equipe |
| 5. Já tenho uma reserva | — | fila da equipe, **prioridade alta** |
| 6. Falar com um atendente | — | fila da equipe |

**Por que o submenu de dúvidas existe.** No nível 3 quem responde "cabe quantas pessoas?" é
a busca em `conteudo/base/`. No nível 1 não há busca nem modelo: sem esse ramo, a pergunta
mais comum do nicho gastava as duas tentativas de `nao_entendi` e caía na fila — o roteiro
sabia menos sobre o salão do que o folheto dele. As cinco respostas (capacidade, o que está
incluso, estrutura, regras da casa, endereço e horários) são resumos dos mesmos arquivos que
alimentam o RAG.

**Isso duplica conteúdo de propósito, e a fronteira é dura:** só entra no roteiro o fato
**estável** — capacidade, metragem, o que a locação inclui, lei de silêncio, endereço. Preço
e disponibilidade continuam vindo da agenda em tempo real, sempre. Mudou a estrutura da casa,
os dois lugares mudam juntos; é o preço de atender sem modelo, e é barato perto de responder
"não sei" para quem perguntou quantas pessoas cabem.

**Nenhum ramo de dúvida termina em caixa terminal.** Um `fim` ou um `handoff` cala o roteiro
para sempre naquela conversa (`engine._consumir`: quem fala dali em diante é gente). Numa
resposta de FAQ isso seria emudecer o bot para quem só tirou uma dúvida — por isso as cinco
respostas voltam para `menu_apos_duvida`.

---

## O formato

```json
{
  "nome": "Atendimento padrão — salão de festas",
  "inicio": "horario",
  "config": {
    "no_handoff": "chamar_equipe",
    "max_invalidas": 2,
    "nao_entendi": "Desculpa, não entendi 😅",
    "palavras_atendente": ["atendente", "humano"]
  },
  "nos": { "<id>": { "tipo": "...", "pos": [x, y], ... } }
}
```

`config.no_handoff` é **obrigatório**: é para onde vai quem sai do roteiro. Sem ele, sair do
roteiro seria ficar sem resposta.

`pos` é a posição da caixa na tela. Ela mora no dado, e não num algoritmo de layout — é isso
que faz o desenho ser o seu desenho, e é o que um editor visual precisaria depois.

### Os nove tipos de caixa

| tipo | campos | saídas |
|---|---|---|
| `mensagem` | `texto` | `proximo` |
| `menu` | `texto`, `opcoes[{rotulo, sinonimos?, proximo}]`, `campo?` | uma por opção |
| `pergunta` | `texto`, `campo`, `validacao: texto\|data\|numero` | `proximo` |
| `condicao` | `campo`, `testes[{op, valor, proximo}]` | `senao` |
| `horario` | — (lê o expediente do painel) | `dentro`, `fora` |
| `agenda` | `campo` (a variável com a data), `sem_livres?` | `livre`, `ocupada`, `bloqueada`, `sem_cadastro`, `sem_preco`, `passado`, `agenda_vazia` |
| `acao` | `acao: fotos\|pre_reserva`, `campo?` | `ok`, `falhou` |
| `handoff` | `texto?`, `resumo?`, `prioridade?` | terminal |
| `fim` | `texto?` | terminal |

O conjunto é **fechado**: cada tipo vive para sempre no banco de todo cliente. Tipo novo do
nicho entra por `app/flow/registro.py::registrar`, não por patch no motor.

### Texto e variáveis

`|||` separa **bolhas**: o roteiro manda duas mensagens, com pausa de digitação entre elas —
a mesma cadência do agente de IA.

`{{variavel}}` é substituído na hora. Vêm de três lugares:

- o que o cliente respondeu (`campo` de uma `pergunta` ou de um `menu`);
- o que o nó `agenda` produziu: `{{data}}`, `{{preco}}`, `{{proximas_livres}}`;
- `{{nome_contato}}`, o nome do perfil do WhatsApp.

**Variável sem valor não vira espaço em branco.** "O valor dessa data é ." sairia para um
cliente e ninguém ficaria sabendo — então o motor trata isso como roteiro quebrado e encaminha
para a equipe com nota interna. Se uma mensagem sua usa `{{preco}}`, ela precisa ser alcançável
só depois de um nó `agenda`.

---

## As quatro regras que não se contornam

**1. Preço não se escreve no roteiro.** O sincronizador reprova qualquer texto com `R$` ou
"reais". Valor tem uma fonte só, a agenda em tempo real: escrito aqui, vira um segundo preço
que estará errado no dia em que você vender uma quinta-feira. Use `{{preco}}`.

**2. As sete saídas do nó `agenda` são obrigatórias.** Motivo sem ramo é o caminho por onde um
caso cai onde não devia — e o ramo perigoso é justamente o que informa valor. Nenhuma delas
pode ir para uma mensagem que fale em dinheiro escrito; só `{{preco}}`, e só depois de `livre`.

As duas que confundem:

- **`sem_cadastro`** é "esse dia não está na agenda". No roteiro padrão ele **responde** e
  oferece `{{proximas_livres}}`: num salão que só vende fim de semana, toda quinta-feira cai
  aqui, e mandar todas para a fila entope a Inbox de conversa que o roteiro sabe resolver.
- **`sem_preco`** é "o dia está na agenda, sem valor definido". Continua indo para `handoff`:
  o cadastro diz que o dia existe, então "não está aberta" contradiz o próprio cadastro, e o
  valor não existe para ser dito. Quem resolve é definir o preço em Agenda.

**`agenda_vazia` é `sem_cadastro` quando não há NENHUMA data futura à venda** — o estado de
todo cliente novo antes de preencher o calendário. Aí a resposta automática vira o bot
despachando sozinho quem pergunta por data, com um "as próximas livres são: nenhuma". Este
ramo vai para `handoff`, sempre.

**3. O roteiro nunca confirma reserva.** O nó `acao: pre_reserva` **segura** a data por 48
horas e avisa a equipe; quem confirma é gente. A saída `falhou` existe porque outra conversa
pode ter levado a data no meio do caminho — quem arbitra é o índice único do banco, não o
roteiro.

**4. Fato estável pode virar texto; preço e disponibilidade, nunca.** É o que separa
"cabemos 220 sentados", que está escrito no roteiro, de "essa data custa X", que só existe
como `{{preco}}`. O critério é simples: se muda quando o dono vende uma quinta-feira, vem da
agenda. Se está no alvará e no contrato, pode ser texto.

---

## Ordem das perguntas: segurar primeiro

No roteiro padrão, `menu_reserva` → opção 1 vai **direto** para `segurar_data`. Nome, tipo de
festa e número de convidados vêm **depois** de a data estar segura.

É decisão de produto, e o motivo é aritmético: três perguntas entre o "quero" e a escrita no
banco são minutos em que outra conversa leva a data de quem já se comprometeu. A reserva não
nasce anônima porque `criar_reserva` grava o nome do perfil do WhatsApp, e o que o cliente
responder depois chega à equipe pela nota de triagem. Se ele parar de responder no meio, a data
continua segura e a equipe já foi avisada.

---

## A conversa que parou no meio

Depois de **1 hora** sem mensagem nenhuma (`CONVERSA_INATIVA_SEGUNDOS`), a conversa é dada
como encerrada e a próxima mensagem do cliente abre uma **nova** — o roteiro recomeça pela
saudação.

O motivo é o caso mais comum de todos: o cliente some em "para qual data você está
pensando?" e volta três horas depois com "oi". Sem o encerramento, "oi" não é data, ele
recebe "Desculpa, não entendi" e na segunda tentativa cai na fila. Ele não errou nada — só
não sabia que a conversa continuava aberta numa pergunta.

**Duas conversas nunca são encerradas assim:** a que um atendente capturou, e a que já foi
encaminhada para a equipe. Sumir com essas seria tirar da fila um cliente que está
esperando resposta.

O encerramento é avaliado quando a mensagem nova chega, e não por um processo em segundo
plano — não existe agendador no projeto. Para desligar, `CONVERSA_INATIVA_SEGUNDOS=0`.

---

## As saídas de emergência

Válidas em **todo** nó que espera resposta, verificadas nesta ordem:

1. **Pediu gente** — qualquer palavra de `config.palavras_atendente` encaminha na hora.
2. **Duas respostas que não encaixam** — a primeira repete a pergunta; a segunda desiste e
   chama a equipe, levando tudo o que já foi coletado.
3. **Vinte caixas num turno** — rede contra um ciclo que o validador não pegou. Encaminha com
   nota interna dizendo onde travou; o cliente nunca vê um bot em looping.

A nota que a equipe recebe traz o resumo do nó **e** o motivo do turno: "O cliente saiu do
roteiro ou pediu atendimento humano" seguido de "pediu para falar com um atendente", ou "mandou
uma foto", ou "respondeu duas vezes algo que o roteiro não entendeu, parado em `X`". Antes só o
primeiro chegava, e todo encaminhamento chegava com a mesma frase genérica.

---

## Quem manda foto, áudio ou figurinha

O roteiro não lê imagem — e isso é resposta, não silêncio.

**Foto, vídeo, documento e figurinha** entram como resposta que não encaixa, no **mesmo
contador** do texto sem sentido: a primeira diz o que ele mandou ("Recebi sua foto! Só que por
aqui eu não consigo ver imagens 😅") e **repete a caixa atual**, com o menu numerado; a segunda
chama a equipe. Contador único de propósito — foto e depois besteira também termina com gente.

Conversa que **começa** por foto é avisada e cumprimentada na sequência: o roteiro não pula a
saudação por causa de um anexo.

**Áudio é diferente: ele é transcrito e o roteiro responde ao conteúdo.** É a única chamada
externa que o nível 1 faz, e ela existe porque nota de voz é como boa parte do público escreve.
Quando a transcrição não vem — download que falha, áudio inaudível — cai na regra acima, com a
frase de áudio.

Nada disso é editável pelo painel hoje: as frases vivem em `app/services/midia.py`, uma só para
o roteiro e para o agente de IA. O que o cliente ouve sobre uma foto não pode mudar quando
alguém troca o modo de atendimento.

---

## Editar o texto pelo painel

Enquanto o roteiro vem do arquivo, a tela mostra tudo em leitura. Clicando em **Editar o
texto por aqui**, o tenant *assume* o roteiro: a partir daí o texto é editado no painel e o
sincronizador para de publicar nele.

**O painel muda texto, e só texto.** Mensagens, rótulos das opções, o resumo que a equipe lê
na nota e a frase de "não entendi". Não muda para onde uma opção leva, quais caixas existem,
o tipo de um nó nem quantas opções um menu tem.

Isso não é uma tela pela metade — é a trava. O corpo da requisição traz apenas o que mudou, e
o servidor aplica sobre o grafo publicado: não existe requisição, nem forjada na mão, capaz de
criar uma saída solta, apagar um ramo de `MOTIVO_*` ou inventar um ciclo. As três regras da
seção anterior continuam valendo — texto com `R$` é reprovado do mesmo jeito.

**Número acompanhado de palavra funciona.** "opção 3", "a 2", "op 1", "3." e "3️⃣" caem na
opção certa. O casamento estrito exige a mensagem inteira em dígitos, e sem essa segunda passada
"opção 3" levava "Desculpa, não entendi" — aconteceu duas vezes no primeiro teste com número
real. O limite é o que evita o tiro no pé: só vale com **um** número na mensagem, e em volta
dele só palavras de escolha ("opção", "op", "nº", "a", "quero", "pode ser", "por favor" — a lista
fechada é `PALAVRAS_DE_ESCOLHA` em `app/flow/engine.py`). "quero para 2 pessoas em julho" não vira
opção 2; segue para a fila da equipe, que é onde ela pertence. E "dia 5", "5 de outubro" e
"seremos 4" também não: a primeira versão aceitava quaisquer três palavras, e data curta — a
pergunta que um salão mais ouve — virava opção de menu sem "não entendi" nenhum. Palavra nova de
escolha entra na lista; o que não está nela é resposta inválida, que repete o menu.

**Cuidado com sinônimo curto.** O casamento é por trecho, e vence a **primeira** opção que
casa, de cima para baixo. `"valor"` na opção 1 engolia "o que está incluso no **valor**?" e
mandava para a pergunta de data quem queria saber o que vem na locação — em silêncio, porque
o roteiro respondeu *alguma* coisa. Sinônimo de opção que está em cima precisa ser específico
(`"qual o valor"`, e não `"valor"`); o genérico pertence à opção de baixo.
`tests/test_flow_edicao.py::test_frase_do_cliente_cai_na_caixa_certa` roda meia centena de
frases reais contra o roteiro do repositório justamente por isto.

**Para voltar ao arquivo:** `--forcar`. Ele descarta o que foi escrito no painel, então é uma
decisão com o mesmo peso de ter assumido:

```bash
python3 scripts/sincronizar_conteudo.py --conta "Seu Salão" --forcar
```

Sem `--forcar`, o sincronizador publica persona e conhecimento normalmente e **pula** o
roteiro, dizendo por quê. O texto que alguém escreveu no painel não some porque outra pessoa
rodou um script.

---

## O simulador

Na mesma tela, abaixo do desenho. Você digita o que um cliente mandaria e vê a conversa
inteira: as bolhas, em qual caixa parou, se foi para a fila da equipe e **qual nota interna a
equipe receberia**.

O que é real: a navegação, as variáveis, a validação das respostas e a **consulta à agenda** —
o preço que aparece é o preço cadastrado, e é para isso que ele serve. O que não acontece:
nada é enviado, nenhuma data é segurada, nenhuma sessão é gravada. `Contexto.simulacao` desvia
todo executor que escreve.

Serve para três coisas, em ordem de importância:

1. **Revisar uma alteração de texto** antes de ela chegar num cliente.
2. **Demonstrar o produto** sem parear um número de WhatsApp.
3. **Conferir os ramos difíceis** — mande uma data não cadastrada e veja que o roteiro
   encaminha sem citar valor.

---

## Como testar antes de ligar para valer

Com o roteiro publicado, o modo **Roteiro (sem IA)** ligado e um número de teste:

| O que mandar | O que tem de acontecer |
|---|---|
| "oi" | saudação + menu numerado (fora do expediente, a outra saudação) |
| "1" e depois uma **data cadastrada e livre** | o valor **exato** da agenda, sem arredondar |
| uma **data não cadastrada** | encaminha **sem citar valor** — é o caso que mais custa |
| uma **data já reservada** | diz que está ocupada e oferece as próximas livres |
| "1" no menu de reserva | pré-reserva aparece em *Agenda* com nota interna, no mesmo turno |
| "2" no menu principal | anúncio e, logo depois, as fotos |
| "3" e depois "1" | responde a capacidade e **volta ao menu** — não encerra a conversa |
| "cabe quantas pessoas?" escrito à mão | mesma resposta, sem passar pelo número |
| "o que está incluso no valor?" | cai em **dúvidas**, não na pergunta de data |
| "4" no menu principal | informa a janela de visitas e encaminha |
| "5" no menu principal | encaminha com **prioridade alta**, sem emoji |
| "300" em "quantas pessoas?" | avisa que nesse tamanho é coquetel, e só então encerra |
| "opção 3" escrito assim | cai em dúvidas, **não** em "não entendi" |
| uma foto | avisa que não vê imagem e **repete o menu**; a conversa fica no mesmo lugar |
| uma segunda foto | encaminha, com nota dizendo que ele mandou foto |
| uma nota de voz | responde ao **conteúdo** do áudio, sem pedir para escrever antes |
| reagir 👍 numa bolha do bot | **nada** — reação não é mensagem |
| sumir e voltar depois de 1 h | conversa nova, saudação de novo — não "não entendi" |
| besteira duas vezes | encaminha |
| "quero falar com um atendente" | encaminha na hora, em qualquer ponto |
| duas mensagens coladas | o roteiro avança **uma vez só** |
| a mesma sequência no simulador | as mesmas respostas, e **nenhuma** reserva criada |

O terceiro caso é o que mais importa e o mais fácil de quebrar sozinho: basta alguém apontar um
ramo do nó `agenda` para uma mensagem que fale de valor. `tests/test_flow_engine.py` cobre isso —
inclusive a data sem cadastro, que responde sem citar preço nenhum da data perguntada.
