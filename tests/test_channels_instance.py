"""Validação do nome da instância de WhatsApp.

`instance` é interpolado no CAMINHO de nove URLs da API do Evolution
(`/instance/connect/{instance}`, `/instance/delete/{instance}`, `/webhook/set/{instance}`…),
todas chamadas com a chave MASTER, que é compartilhada por todos os tenants da plataforma.

O httpx normaliza segmentos `..` antes de enviar. Então um admin que criasse uma conexão
com `instance = "../../instance/delete/salao-vizinho"` não estava escolhendo um nome:
estava escolhendo outra rota administrativa, no Evolution do vizinho.

`TestNormalizacaoDoHttpx` documenta o mecanismo — se um dia o httpx parar de normalizar,
o teste falha e alguém relê este arquivo antes de afrouxar o regex.
"""

import httpx
import pytest

from app.schemas import CreateWhatsAppConnectionSchema

pytestmark = pytest.mark.asyncio


class TestFormatoDaInstancia:
    @pytest.mark.parametrize(
        "valor",
        [
            "../../instance/delete/vizinho",
            "..%2f..%2finstance",
            "salao/../outro",
            "salao com espaco",
            "salao?x=1",
            "salao#frag",
            "sa",  # curto demais
            "",
            "x" * 65,  # longo demais
            "salão-acentuado",
        ],
    )
    async def test_recusa(self, valor):
        with pytest.raises(Exception):
            CreateWhatsAppConnectionSchema(instance=valor)

    @pytest.mark.parametrize(
        "valor", ["otto-test", "salao_do_ze", "Salao123", "abc", "x" * 64]
    )
    async def test_aceita(self, valor):
        assert CreateWhatsAppConnectionSchema(instance=valor).instance == valor


class TestNormalizacaoDoHttpx:
    async def test_ponto_ponto_vira_outra_rota(self):
        """Por que o regex acima existe: o `..` não chega ao servidor como texto."""
        url = httpx.URL(
            "http://evolution:8080/instance/connect/" + "../../manager/instances"
        )
        assert url.path == "/manager/instances"

    async def test_nome_valido_fica_no_lugar(self):
        url = httpx.URL("http://evolution:8080/instance/connect/" + "otto-test")
        assert url.path == "/instance/connect/otto-test"
