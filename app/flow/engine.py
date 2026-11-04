"""O motor do roteiro: recebe a mensagem e devolve a MESMA decisão que o LLM devolveria.

`decidir()` é a irmã determinística de `llm.agent_turn`. Devolve
`{reply, captured, status, summary, priority, sentiment}` — e por isso todo o resto do
turno (bolhas com cadência, handoff atômico, nota de triagem, ações do salão,
persistência) é o código que já existe, sem uma linha nova.

**Como um turno funciona.** A conversa fica parada num nó que espera resposta. Chegando a
mensagem, o motor consome a resposta ali, escolhe a saída e CAMINHA — executando nós que
não esperam nada (mensagem, condição, horário, agenda, ação) e acumulando bolhas — até
esbarrar no próximo nó de espera ou num terminal. Só então reivindica o avanço
(`flow_store.avancar`, UPDATE condicional) e devolve a decisão.

A caminhada é pura até a reivindicação, com uma exceção deliberada: a pré-reserva escreve
no banco durante o percurso, porque o motor precisa do resultado para escolher entre
"segurei sua data" e "essa data acabou de ser fechada". Ver `app/services/salao_fluxo.py`.

**Nada aqui confia no grafo.** Nó que sumiu, variável sem valor, saída inexistente e ciclo
não pego pelo validador terminam todos no mesmo lugar: fila da equipe, com nota interna
dizendo o que quebrou. Roteiro com defeito degrada para gente, nunca para uma frase
mutilada no WhatsApp de um cliente.
"""

import logging
import re
import unicodedata
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.flow import horario as horario_mod
from app.flow import schema, texto as texto_mod
from app.flow.registro import Contexto, executor_de
from app.services import flow_store, midia as midia_txt

log = logging.getLogger("uvicorn.error")

# Os dois motivos de silêncio que só o roteiro produz. Moram AQUI e `SkipReason` os
# importa, em vez de cada lado repetir a string: são gravados numa coluna que a UI traduz,
# e duas cópias divergiriam em silêncio na primeira vez que alguém renomeasse uma.
SKIP_FLUXO_AUSENTE = "fluxo_ausente"
SKIP_FLUXO_CORRIDA = "fluxo_corrida"

# Conversa que não existe: o simulador precisa de um id para a assinatura dos
# executores, e nenhum deles escreve durante a simulação (ver `Contexto.simulacao`).
_CONVERSA_FICTICIA = UUID("00000000-0000-0000-0000-000000000000")

NAO_ENTENDI_PADRAO = "Desculpa, não entendi 😅"

# Limite de caracteres guardados de uma resposta livre. O cliente às vezes cola um
# parágrafo, e isto vira nota interna e perfil permanente do contato.
MAX_CARACTERES_RESPOSTA = 300

# As únicas palavras que podem acompanhar um número para ele valer como escolha de menu,
# já normalizadas (sem acento, minúsculas). Ver `_Turno._numero_solto`.
PALAVRAS_DE_ESCOLHA = frozenset(
    "opcao opcoes op numero num n nº no item a o e "
    "quero escolho prefiro pode ser por favor".split()
)


class _Quebrado(Exception):
    """Roteiro inconsistente em runtime. Sempre vira handoff com nota interna."""


def _normalizar(t: str) -> str:
    sem_acento = "".join(
        c
        for c in unicodedata.normalize("NFD", (t or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.split())


async def decidir(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    session: dict,
    bot,
    mensagem: str,
    hoje: date | None = None,
    midia: str | None = None,
) -> dict | None:
    """A decisão do turno, ou `None` quando o bot não deve falar (motivo na sessão).

    `midia` é o `ContentType` de uma mensagem que NÃO é texto (o áudio só chega aqui
    quando a transcrição falhou). Ela entra como turno de verdade, e não como caso
    especial fora do motor: é assim que o contador de respostas inválidas, o handoff e a
    nota interna valem para foto exatamente como valem para texto sem sentido. O caminho
    antigo respondia uma frase fixa fora do roteiro e, da segunda mídia em diante, ficava
    mudo — sem nota e sem encaminhar.
    """
    publicado = await flow_store.carregar(db, tenant_id)
    if not publicado or not (publicado["grafo"] or {}).get("nos"):
        session["skip_reason"] = SKIP_FLUXO_AUSENTE
        log.warning("fluxo: tenant %s está em modo fluxo e não tem roteiro", tenant_id)
        return None

    turno = _Turno(publicado, session, conv, bot, hoje)
    try:
        decisao = await turno.rodar(db, tenant_id, conversation_id, mensagem, midia)
    except _Quebrado as e:
        decisao = await turno.desviar_para_equipe(
            db, tenant_id, conversation_id, str(e)
        )

    ganhou = await flow_store.avancar(
        db, conversation_id, turno.turno_lido, turno.estado_novo
    )
    if not ganhou:
        session["skip_reason"] = SKIP_FLUXO_CORRIDA
        return None

    session["variables"]["fluxo"] = turno.estado_novo
    for nota in turno.notas:
        await _nota_interna(db, tenant_id, conversation_id, nota)
    return decisao


class _Turno:
    """Estado de UMA passagem pelo roteiro. Descartável — nada aqui sobrevive ao turno."""

    def __init__(
        self,
        publicado: dict,
        session: dict,
        conv,
        bot,
        hoje: date | None,
        simulacao: bool = False,
    ):
        self.simulacao = simulacao
        self.grafo = publicado["grafo"]
        self.versao = publicado["versao"]
        self.nos: dict = self.grafo.get("nos") or {}
        self.config: dict = self.grafo.get("config") or {}
        self.session = session
        self.conv = conv
        self.bot = bot
        self.hoje = hoje

        estado = dict(session["variables"].get("fluxo") or {})
        self.turno_lido = int(estado.get("turno") or 0)
        self.no_atual = estado.get("no")
        self.invalidas = int(estado.get("invalidas") or 0)

        self.captured: dict = dict(session["variables"].get("intake") or {})
        # O que nós de execução produziram em turnos ANTERIORES. Ver `Contexto.derivadas`:
        # a mensagem "segurei o dia {{data}}" sai três turnos depois de a agenda resolver.
        self.derivadas: dict = dict(estado.get("vars") or {})
        self.variaveis: dict = {**self.captured, **self.derivadas}
        self.variaveis.setdefault(
            "nome_contato", getattr(conv, "contact_name", "") or ""
        )
        self.bolhas: list[str] = []
        self.notas: list[str] = []
        self.status = "gathering"
        self.prioridade = "normal"
        self.resumo = ""
        self.estado_novo: dict = {}

    # ─────────────────────────── o turno ───────────────────────────

    async def rodar(
        self,
        db: AsyncSession,
        tenant_id: UUID,
        conversation_id: UUID,
        mensagem: str,
        midia: str | None = None,
    ) -> dict:
        self.ctx = Contexto(
            db=db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            conv=self.conv,
            session=self.session,
            captured=self.captured,
            variaveis=self.variaveis,
            derivadas={},
            bot=self.bot,
            hoje=self.hoje,
            simulacao=self.simulacao,
        )

        if self.no_atual is None:
            # Conversa que COMEÇA por foto: avisa que não lê imagem e segue para a
            # saudação. Ignorar a foto e cumprimentar seria responder ao lado.
            if midia:
                self.bolhas.append(midia_txt.texto(midia))
            destino = self.grafo.get("inicio")
            if destino not in self.nos:
                raise _Quebrado(f"`inicio` aponta para {self.grafo.get('inicio')!r}")
        else:
            destino = await self._consumir(mensagem, midia)
            if (
                destino is None
            ):  # já respondeu (repetiu a pergunta) ou ficou em silêncio
                return self._decisao(self.no_atual)

        parou_em = await self._caminhar(destino)
        return self._decisao(parou_em)

    async def _consumir(self, mensagem: str, midia: str | None = None) -> str | None:
        """Interpreta a resposta no nó em que a conversa parou. `None` = já resolvido."""
        no = self.nos.get(self.no_atual)
        if no is None:
            raise _Quebrado(
                f"o nó `{self.no_atual}` não existe mais no roteiro "
                f"(a conversa estava na versão {self.turno_lido and self.versao})"
            )
        espec = schema.espec_de(no.get("tipo"))
        if espec is None or espec.terminal:
            # Roteiro encerrado. Quem fala com este cliente agora é gente — repetir o
            # roteiro do começo seria cumprimentar de novo alguém no meio do atendimento.
            return None
        if not espec.espera_resposta:
            raise _Quebrado(
                f"a conversa parou em `{self.no_atual}`, que não espera resposta"
            )

        if midia:
            return self._midia_nao_encaixa(no, midia)

        if self._pediu_atendente(mensagem):
            self.invalidas = 0
            self.resumo = "O cliente pediu para falar com um atendente."
            return self._no_de_handoff()

        destino = (
            self._resposta_de_menu(no, mensagem)
            if no["tipo"] == "menu"
            else self._resposta_de_pergunta(no, mensagem)
        )
        if destino is not None:
            self.invalidas = 0
            return destino

        self.invalidas += 1
        if self.invalidas >= int(
            self.config.get("max_invalidas") or schema.MAX_INVALIDAS_PADRAO
        ):
            self.invalidas = 0
            self.resumo = (
                "O cliente respondeu duas vezes algo que o roteiro não entendeu, "
                f"parado em `{self.no_atual}`."
            )
            return self._no_de_handoff()

        self.bolhas.append(self.config.get("nao_entendi") or NAO_ENTENDI_PADRAO)
        self.bolhas.append(self._texto_do_no(no))
        return None

    def _midia_nao_encaixa(self, no: dict, midia: str) -> str | None:
        """Mídia é resposta que não encaixa — mesmo contador, mesma saída de emergência.

        Reusar `invalidas` em vez de criar um contador só para mídia é o ponto: quem
        manda uma foto e depois escreve qualquer coisa também chega na equipe, e quem
        insiste na foto chega na segunda. O roteiro não fica mudo em nenhum dos dois.

        A repetição do texto do nó é o que evita o beco: a frase genérica antiga
        ("me conta em uma linha o que você precisa") convidava a texto livre, que num
        menu vira mais uma resposta inválida.
        """
        self.invalidas += 1
        if self.invalidas >= int(
            self.config.get("max_invalidas") or schema.MAX_INVALIDAS_PADRAO
        ):
            self.invalidas = 0
            rotulo = midia_txt.rotulo(midia)
            self.resumo = (
                f"O cliente mandou {rotulo} e o roteiro não lê esse tipo de mensagem, "
                f"parado em `{self.no_atual}`. Vale olhar o que ele enviou."
            )
            return self._no_de_handoff()

        self.bolhas.append(midia_txt.texto(midia))
        self.bolhas.append(self._texto_do_no(no))
        return None

    async def _caminhar(self, destino: str) -> str:
        """Executa nós até parar num que espera resposta ou num terminal."""
        for _ in range(schema.MAX_NOS_POR_TURNO):
            no = self.nos.get(destino)
            if no is None:
                raise _Quebrado(f"saída aponta para `{destino}`, que não existe")
            espec = schema.espec_de(no.get("tipo"))
            if espec is None:
                raise _Quebrado(f"`{destino}` tem tipo desconhecido {no.get('tipo')!r}")

            if espec.espera_resposta:
                self.bolhas.append(self._texto_do_no(no))
                return destino
            if espec.terminal:
                self._encerrar(destino, no)
                return destino

            destino = await self._executar(destino, no)
        raise _Quebrado(
            f"o roteiro passou de {schema.MAX_NOS_POR_TURNO} nós num turno só, "
            f"parando em `{destino}` — provavelmente um ciclo"
        )

    async def _executar(self, nid: str, no: dict) -> str:
        tipo = no["tipo"]
        if tipo == "mensagem":
            self.bolhas.append(self._texto_do_no(no))
            return self._saida(nid, no, "proximo")
        if tipo == "horario":
            dentro = horario_mod.dentro_do_expediente(
                getattr(self.bot, "horario", None)
            )
            return self._saida(nid, no, "dentro" if dentro else "fora")
        if tipo == "condicao":
            return self._condicao(nid, no)

        executor = executor_de(tipo)
        if executor is None:
            raise _Quebrado(f"`{nid}`: nenhum executor registrado para o tipo {tipo!r}")
        self.ctx.derivadas = {}
        saida = await executor(self.ctx, no)
        produzidas = {
            str(k): str(v)[:MAX_CARACTERES_RESPOSTA]
            for k, v in self.ctx.derivadas.items()
        }
        self.derivadas.update(produzidas)
        self.variaveis.update(produzidas)
        self.notas.extend(self.ctx.notas)
        self.ctx.notas.clear()
        return self._saida(nid, no, saida)

    def _condicao(self, nid: str, no: dict) -> str:
        valor = _normalizar(str(self.variaveis.get(no.get("campo"), "")))
        for teste in no.get("testes") or []:
            if not isinstance(teste, dict):
                continue
            if _compara(
                valor, str(teste.get("op") or "igual"), str(teste.get("valor", ""))
            ):
                return teste["proximo"]
        return self._saida(nid, no, "senao")

    def _encerrar(self, nid: str, no: dict) -> None:
        # O resumo do NÓ é o "o quê" (é o mesmo para todo mundo que cai ali); o que o
        # turno calculou é o "porquê" — pediu atendente, não entendeu duas vezes, mandou
        # foto. Substituir um pelo outro jogava fora justamente a parte que a equipe
        # precisa antes de assumir: todo encaminhamento chegava com a mesma frase
        # genérica, e descobrir o motivo exigia ler a conversa inteira.
        self.resumo = (
            "\n".join(p for p in (no.get("resumo") or "", self.resumo) if p)
            or self.resumo
        )
        if no.get("prioridade") == "alta":
            self.prioridade = "high"
        if no["tipo"] == "handoff":
            self.status = "ready_to_handoff"
            self.bolhas.append(
                self._texto_do_no(no) if no.get("texto") else self.bot.transfer_message
            )
            return
        # `fim`: ninguém é chamado, então o resumo do que foi coletado precisa virar nota
        # aqui — senão o que o cliente respondeu depois da pré-reserva morre na sessão.
        if no.get("texto"):
            self.bolhas.append(self._texto_do_no(no))
        dados = [
            f"- {k}: {v}"
            for k, v in self.captured.items()
            if not k.startswith("_") and str(v).strip()
        ]
        if dados:
            self.notas.append("Roteiro concluído\n" + "\n".join(dados))

    # ─────────────────────────── resposta do cliente ───────────────────────────

    def _pediu_atendente(self, mensagem: str) -> bool:
        alvo = _normalizar(mensagem)
        palavras = (
            self.config.get("palavras_atendente") or schema.PALAVRAS_ATENDENTE_PADRAO
        )
        return any(_normalizar(p) in alvo for p in palavras if p)

    @staticmethod
    def _numero_solto(opcoes: list, alvo: str) -> dict | None:
        """ "opção 3", "a 2", "op 1" — o número que veio acompanhado de palavra.

        O casamento estrito exige a mensagem inteira em dígitos, e por isso "opção 3"
        levava "Desculpa, não entendi". Aconteceu duas vezes no primeiro teste com número
        real: é das formas mais naturais de responder um menu numerado.

        Vem DEPOIS de rótulo e sinônimo de propósito — a armadilha do sinônimo curto
        (`GUIA-FLUXO-SALAO.md`) continua valendo, e quem casa por texto continua ganhando.

        As duas restrições são o que impede o tiro no pé: **um** número só (senão "dia 20,
        umas 200 pessoas" viraria escolha de menu) e, em volta dele, só vocabulário de
        ESCOLHA (`PALAVRAS_DE_ESCOLHA`). A primeira versão aceitava quaisquer três
        palavras, e "dia 5", "5 de outubro" e "seremos 4" — data e contagem, justamente o
        que um salão mais ouve — viravam as opções 5, 5 e 4 do menu principal, sem "não
        entendi" nenhum. Lista fechada erra para o lado seguro: frase fora dela é a
        resposta inválida de sempre, que repete o menu.
        """
        numeros = re.findall(r"\d+", alvo)
        if len(numeros) != 1:
            return None
        if any(p not in PALAVRAS_DE_ESCOLHA for p in re.findall(r"[^\W\d_]+", alvo)):
            return None
        i = int(numeros[0]) - 1
        return opcoes[i] if 0 <= i < len(opcoes) else None

    def _resposta_de_menu(self, no: dict, mensagem: str) -> str | None:
        opcoes = no.get("opcoes") or []
        alvo = _normalizar(mensagem)
        escolhida = None

        so_digitos = "".join(c for c in alvo if c.isdigit())
        if so_digitos and so_digitos == alvo.replace(" ", ""):
            i = int(so_digitos) - 1
            if 0 <= i < len(opcoes):
                escolhida = opcoes[i]
        if escolhida is None:
            for op in opcoes:
                termos = [op.get("rotulo", "")] + list(op.get("sinonimos") or [])
                if any(t and _normalizar(t) in alvo for t in termos):
                    escolhida = op
                    break
        if escolhida is None:
            escolhida = self._numero_solto(opcoes, alvo)
        if escolhida is None:
            return None
        if no.get("campo"):
            self._guardar(no["campo"], escolhida.get("rotulo", ""))
        return escolhida["proximo"]

    def _resposta_de_pergunta(self, no: dict, mensagem: str) -> str | None:
        valor = _valida(no.get("validacao", "texto"), mensagem, self.hoje)
        if valor is None:
            return None
        self._guardar(no["campo"], valor)
        return self._saida(None, no, "proximo")

    def _guardar(self, campo: str, valor: str) -> None:
        valor = str(valor).strip()[:MAX_CARACTERES_RESPOSTA]
        self.captured[campo] = valor
        self.variaveis[campo] = valor

    # ─────────────────────────── auxiliares ───────────────────────────

    def _saida(self, nid: str | None, no: dict, nome: str) -> str:
        destino = (no.get("saidas") or {}).get(nome) or (
            no.get(nome) if nome in ("proximo", "senao") else None
        )
        if not destino:
            raise _Quebrado(f"`{nid or no.get('tipo')}` não tem a saída `{nome}`")
        return destino

    def _no_de_handoff(self) -> str:
        destino = self.config.get("no_handoff")
        if destino not in self.nos:
            raise _Quebrado(
                f"`config.no_handoff` aponta para {destino!r}, que não existe"
            )
        return destino

    def _texto_do_no(self, no: dict) -> str:
        partes = [self._render(no.get("texto") or "")]
        if no.get("tipo") == "menu":
            linhas = [
                f"{i + 1}. {self._render(op.get('rotulo', ''))}"
                for i, op in enumerate(no.get("opcoes") or [])
            ]
            if linhas:
                partes.append("\n".join(linhas))
        return "\n\n".join(p for p in partes if p)

    def _render(self, bruto: str) -> str:
        pronto, faltando = texto_mod.renderizar(bruto, self.variaveis)
        if faltando:
            raise _Quebrado(
                "faltou valor para "
                + ", ".join(f"{{{{{n}}}}}" for n in sorted(set(faltando)))
            )
        return " ".join(pronto.split(" "))

    async def desviar_para_equipe(
        self, db: AsyncSession, tenant_id: UUID, conversation_id: UUID, motivo: str
    ) -> dict:
        """Roteiro quebrou em runtime: chama gente e registra o defeito na thread."""
        log.error("fluxo: %s (conversa %s)", motivo, conversation_id)
        self.notas.append(
            f"O roteiro travou e a conversa foi encaminhada.\n- motivo: {motivo}\n"
            f"- versão do fluxo: {self.versao}\n"
            "Corrija o roteiro em Configurações › Fluxo antes que outro cliente caia aqui."
        )
        self.status = "ready_to_handoff"
        self.resumo = self.resumo or "O roteiro travou; a conversa veio para a equipe."
        self.bolhas = [self.bot.transfer_message]
        destino = self.config.get("no_handoff")
        return self._decisao(destino if destino in self.nos else self.no_atual)

    def _decisao(self, parou_em: str | None) -> dict:
        self.estado_novo = {
            "no": parou_em,
            "turno": self.turno_lido + 1,
            "invalidas": self.invalidas,
            "versao": self.versao,
            "vars": self.derivadas,
        }
        return {
            "reply": schema.SEPARADOR_BOLHA.join(b for b in self.bolhas if b.strip()),
            "captured": self.captured,
            "status": self.status,
            "summary": self.resumo,
            "priority": self.prioridade,
            # O roteiro não mede sentimento: dizer "neutro" é honesto, inventar não seria.
            "sentiment": "neutral",
        }


def _compara(valor: str, op: str, alvo: str) -> bool:
    alvo_norm = _normalizar(alvo)
    if op in ("igual", "=="):
        return valor == alvo_norm
    if op in ("contem", "contém"):
        return alvo_norm in valor
    if op in ("maior", ">", "menor", "<"):
        try:
            a, b = float(_so_numero(valor) or "x"), float(alvo)
        except ValueError:
            return False
        return a > b if op in ("maior", ">") else a < b
    return False


def _so_numero(t: str) -> str:
    return "".join(c for c in t if c.isdigit())


def _valida(tipo: str, mensagem: str, hoje: date | None) -> str | None:
    bruto = (mensagem or "").strip()
    if not bruto:
        return None
    if tipo == "numero":
        digitos = _so_numero(bruto)
        return digitos or None
    if tipo == "data":
        # `extrair_datas` mora em `services/datas`: é o parser compartilhado de
        # data em português do projeto ("15/08", "15 de agosto", "sábado que vem"). Escrever
        # um segundo aqui seria duas implementações da mesma regra divergindo com o tempo.
        from app.services.datas import extrair_datas

        datas = extrair_datas(bruto, hoje)
        return datas[0].isoformat() if datas else None
    return bruto


async def _nota_interna(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, texto: str
) -> None:
    # Import tardio: `bot_orchestrator` importa este módulo, e no topo seria ciclo. Mesmo
    # padrão (e mesmo motivo) de `salao_acoes._nota`.
    from app.services.bot_orchestrator import _insert_internal_note

    await _insert_internal_note(db, tenant_id, conversation_id, texto)


# ─────────────────────────── simulador do painel ───────────────────────────


class _ConversaDeMentira:
    """O mínimo que o motor lê de uma conversa. Sem linha em `conversations`."""

    contact_name = "Cliente de teste"
    contact_id = None
    contact_phone = None
    channel = "WHATSAPP"


async def simular(
    db: AsyncSession,
    tenant_id: UUID,
    bot,
    mensagens: list[str],
    grafo: dict | None = None,
    hoje: date | None = None,
) -> list[dict]:
    """Roda o roteiro do começo contra as mensagens dadas. NÃO escreve, NÃO envia.

    Existe por dois motivos práticos. O primeiro é revisar: editar o texto de uma caixa no
    painel sem poder ver a conversa resultante é editar no escuro. O segundo é demonstrar
    o produto sem parear um número de WhatsApp.

    O que é real aqui: a navegação, a interpolação de variáveis, a validação das respostas
    e a **consulta à agenda** — ver o preço verdadeiro da data é justamente o ponto. O que
    não é: nada é gravado. A sessão vive nesta função, `flow_store.avancar` não é chamado
    e os executores que escrevem são desviados por `ctx.simulacao`.

    `grafo` permite simular um rascunho antes de publicá-lo; sem ele, roda o que está no ar.
    """
    if grafo is None:
        publicado = await flow_store.carregar(db, tenant_id)
        if not publicado or not (publicado["grafo"] or {}).get("nos"):
            return []
    else:
        publicado = {"grafo": grafo, "versao": 0}

    session: dict = {"variables": {}, "handoff": False}
    conv = _ConversaDeMentira()
    passos: list[dict] = []

    for mensagem in mensagens:
        turno = _Turno(publicado, session, conv, bot, hoje, simulacao=True)
        try:
            decisao = await turno.rodar(db, tenant_id, _CONVERSA_FICTICIA, mensagem)
        except _Quebrado as e:
            decisao = {
                "reply": bot.transfer_message,
                "captured": turno.captured,
                "status": "ready_to_handoff",
                "summary": str(e),
                "priority": "normal",
                "sentiment": "neutral",
            }
            turno.notas.append(f"O roteiro travaria aqui: {e}")
            turno._decisao(turno.config.get("no_handoff"))

        session["variables"]["fluxo"] = turno.estado_novo
        if decisao["captured"]:
            session["variables"]["intake"] = decisao["captured"]
        passos.append(
            {
                "cliente": mensagem,
                "bolhas": [
                    b
                    for b in decisao["reply"].split(schema.SEPARADOR_BOLHA)
                    if b.strip()
                ],
                "no": turno.estado_novo.get("no"),
                "encaminha": decisao["status"] == "ready_to_handoff",
                "notas": list(turno.notas),
                "coletado": dict(decisao["captured"]),
            }
        )
        if decisao["status"] == "ready_to_handoff":
            session["handoff"] = True
    return passos
