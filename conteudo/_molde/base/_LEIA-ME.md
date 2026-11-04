# Como escrever os documentos desta pasta

Este arquivo começa com `_` e por isso **não é indexado** — ele existe para que a
explicação não vire vetor. Todo `.md` daqui que não comece com `_` é enviado à base
vetorial pelo `scripts/sincronizar_conteudo.py`.

## O contrato

```yaml
---
title: Estrutura do espaço
---
```

- `title` é obrigatório e **não é decoração**: ele é colado na frente de cada trecho
  recuperado, então o modelo o lê em toda resposta que usar este documento. Escreva um
  rótulo de assunto ("Estrutura do espaço"), não um nome de arquivo.
- O `source` é o nome do arquivo sem `.md`. **Renomear o arquivo re-indexa tudo** (apaga a
  source antiga e cria outra), o que custa uma chamada de embeddings — renomeie só quando
  o assunto mudar de verdade.
- O corpo depois do `---` vai **verbatim** para a ingestão.

## As regras que importam

O texto é cortado em pedaços de 1.200 caracteres com 200 de sobreposição, e a busca
entrega **um pedaço isolado** ao modelo — sem título, sem o parágrafo anterior, sem o
resto do documento.

- **Repita o sujeito.** "O [NOME DO SALÃO] tem…", nunca "aqui", "o espaço" ou "nós".
- **Um assunto por parágrafo**, e o parágrafo começa dizendo qual é o assunto.
- **Nada de "além disso", "como dito acima", "no caso anterior".**
- Números e condições no mesmo bloco da regra a que pertencem.
- Evite tabela e markdown pesado: vira ruído no vetor. Prosa curta funciona melhor.
- 2.000 a 4.000 caracteres por arquivo é o alvo.

## O que NUNCA entra aqui

**Valor em dinheiro.** Preço tem uma fonte só — a agenda, em tempo real. Um número escrito
aqui vira um segundo preço competindo com ela, e é o texto estático que estará errado
amanhã. O sincronizador reprova qualquer arquivo com `R$` ou "reais".

Disponibilidade também não: que dias a casa vende é a configuração da Agenda.

## Proibição não mora aqui

A busca semântica traz o que **se parece** com a pergunta, e "quanto custa?" não se parece
com "não informamos preço". Trava que depende da busca acertar é trava que depende de
sorte — toda proibição vive na persona (`conteudo/persona.md`).
