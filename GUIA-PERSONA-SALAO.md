# Persona do agente — Salão de Festas

Este documento é o **conteúdo** que faz a agenda e a galeria funcionarem. Sem ele o código
está no ar e o agente não usa nenhum dos dois.

Para as regras gerais de como escrever persona e conhecimento, ver `GUIA-CONTEUDO-AGENTE.md`
— aqui está só o que é específico deste derivado.

**Onde ele mora: [`conteudo/persona.md`](conteudo/persona.md).** O arquivo já traz o texto
inteiro, na ordem correta; preencha o que está entre `[ ]` e publique com:

```bash
python3 scripts/sincronizar_conteudo.py --check                    # valida
python3 scripts/sincronizar_conteudo.py --conta "Seu Salão" --dry-run
python3 scripts/sincronizar_conteudo.py --conta "Seu Salão"
```

Depois disso a tela `/agente` mostra persona e conhecimento **em leitura**, apontando para
os arquivos. Copiar e colar no painel foi como isso funcionou até aqui, e é justamente o
que o parágrafo seguinte explica por que era arriscado.

---

## Por que a persona é obrigatória aqui

Duas coisas dependem exclusivamente dela:

1. **Enviar as fotos do espaço.**
2. **Segurar uma data (pré-reserva).**

O agente do Otto é uma chamada só, sem tool-calling — ele não "executa" nada. O que ele
faz é devolver um JSON, e o campo `captured` é um dicionário livre que o modelo preenche
**a partir da persona**. O derivado lê duas chaves de lá e age
(`app/services/salao_acoes.py`). Foi esta a escolha que evitou modificar
`app/ai/llm.py`, o arquivo mais caro de manter divergente da base (`PLATAFORMA.md` §5).

Consequência prática: **tirar as instruções abaixo da persona desliga os dois recursos**,
sem nenhum erro em log. O que continua funcionando sozinho é a *informação* de data e
valor — essa entra no conhecimento do turno pelo `salao_contexto`, sem depender de o
modelo pedir nada.

A agenda em si nunca depende de o modelo se comportar: quem impede overbooking é o índice
único no banco, e quem recusa data sem valor cadastrado é o service. A persona decide
quando ele *tenta*, não o que acontece.

---

## Persona — onde ela está

O texto completo vive em [`conteudo/persona.md`](conteudo/persona.md), e é ele que o
modelo recebe **byte a byte**: o arquivo não tem título nem comentário porque cada
caractere é enviado em toda mensagem trocada.

A ordem das seções não é arbitrária. A persona é o **último** bloco do system prompt
(`app/ai/llm.py::_agent_system`), e o começo e o fim de um bloco são as posições de
maior peso — o meio é onde instrução se perde. Por isso:

1. **As proibições vêm logo depois da identidade.** A trava de preço é a regra mais
   cara de furar; ela não pode morar no meio do texto.
2. **O PROTOCOLO DE AÇÕES vem por último**, encostado na mensagem do cliente. É o que
   faz fotos e pré-reserva existirem, e o único cuja falha é silenciosa.
3. **`COMO VOCÊ FALA` é curto de propósito.** O prompt genérico já pede ~um emoji por
   mensagem e nenhum em cancelamento; repetir isso é pagar duas vezes em todo turno.
   Ali fica só o desvio — o repertório de festa deste nicho.

A linha de identidade diz **"assistente virtual"**, não "atendente". O cliente tem de
saber com o que está falando, e em teste real o sistema fazia o oposto: o prompt genérico
começava com "você é uma atendente humana". *Como* se apresentar (na primeira mensagem,
uma vez só, e o que responder a "você é um robô?") é regra do prompt genérico
(`app/ai/llm.py::_agent_system`) e vale para todo tenant — **não se escreve na persona**,
que tem só 4.000 caracteres para gastar no que é específico deste salão.

`tests/test_conteudo.py` reprova se o arquivo perder `_acao_fotos`, `_acao_pre_reserva`,
o formato `AAAA-MM-DD` ou a seção `O QUE VOCÊ NUNCA FAZ` (esta última via
`scripts/sincronizar_conteudo.py::validar`, que o teste chama).

---

## Conhecimento essencial e documentos

[`conteudo/conhecimento.md`](conteudo/conhecimento.md) fica com o punhado de fatos que
precisam estar presentes em **toda** resposta: endereço, capacidade, janela de visita, a
repetição da trava de preço e a lista do que a equipe precisa receber. Ele é cobrado em
cada mensagem, então é o campo com a maior disciplina de tamanho.

O volume — estrutura, o que está incluso, regras da casa, processo de contratação e FAQ —
mora em [`conteudo/base/`](conteudo/base/), um arquivo por assunto que o cliente pergunta.
Esses são recuperados por busca semântica e pagos só quando relevantes. Como escrevê-los
está em [`conteudo/base/_LEIA-ME.md`](conteudo/base/_LEIA-ME.md).

**Três coisas não entram em nenhum dos dois: preço, disponibilidade e os dias da semana em
que a casa funciona.** Os três vêm da agenda, em tempo real. Escritos no conteúdo, viram
uma segunda verdade competindo com ela dentro do mesmo prompt — e é o texto estático que
estará errado no dia em que o dono vender uma quinta-feira. O sincronizador reprova
qualquer arquivo com `R$` ou "reais".

---

## Como testar antes de ligar para valer

Com o agente ligado e um número de teste, na mesma conversa:

| O que mandar | O que tem de acontecer |
|---|---|
| "tem foto do salão?" | as fotos ativas chegam, na ordem, uma vez só |
| "quanto fica dia [DATA CADASTRADA]?" | o valor **exato** da agenda, sem arredondar |
| "e dia [DATA NÃO CADASTRADA]?" | diz que confirma com um atendente, **sem citar valor** |
| "e no [DATA JÁ RESERVADA]?" | diz que está ocupada e oferece as próximas livres |
| "quero segurar o [DATA LIVRE]" | pré-reserva aparece em *Agenda*, com nota interna na thread |
| "consegue fazer por menos?" | não inventa desconto; encaminha |
| "preciso cancelar, tive um problema" | responde **sem nenhum emoji** |

O terceiro caso é o que mais falha e o que mais custa. Se o agente estimar um valor para
data não cadastrada, reforce a primeira proibição — e conferi que ela está na **persona**,
não só no conhecimento: a busca semântica não garante trazer uma regra na hora certa.
