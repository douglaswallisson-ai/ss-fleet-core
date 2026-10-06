"""Regras do contrato (endpoints/contratos.py) — sem banco."""

from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.contratos import _calcular, _limpar, _validar_novo, cnpj_valido


def novo(**kw):
    base = {"nome_grupo": "Cliente Teste", "razao_social": "Cliente Teste Ltda", "cnpj": "11.222.333/0001-81",
            "segmento": "carga", "data_inicio": "2026-10-01", "data_fim": "2028-09-30", "qtd_veiculos": 10,
            "valor_parcela": 1500, "contato_nome": "Ana", "contato_email": "ana@cliente.com.br"}
    base.update(kw)
    return base


def test_cnpj():
    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("11.222.333/0001-82")
    assert not cnpj_valido("11111111111111")


def test_contrato_novo_valido_passa():
    _validar_novo(novo(), [])


@pytest.mark.parametrize("campo,valor", [("cnpj", "123"), ("data_fim", "2025-01-01"), ("qtd_veiculos", 0),
                                         ("valor_parcela", 0), ("contato_email", "sem-arroba"), ("razao_social", "")])
def test_contrato_novo_recusa(campo, valor):
    with pytest.raises(HTTPException) as e:
        _validar_novo(novo(**{campo: valor}), [])
    assert e.value.detail["field"] == campo


def test_nome_de_grupo_repetido():
    with pytest.raises(HTTPException):
        _validar_novo(novo(), [{"nome_grupo": "cliente teste"}])


def test_calculo_total_e_por_veiculo():
    c = _calcular({"data_inicio": "2026-01-01", "data_fim": "2026-12-31", "valor_parcela": 1000, "valor_implantacao": 500, "qtd_veiculos": 4})
    assert c["tempo_meses_calc"] == 12 and c["valor_total_calc"] == 12500 and c["valor_por_veiculo"] == 250


def test_vencido_nao_encerra():
    """Passou do fim: só marca vencido (quem encerra é a pessoa)."""
    c = _calcular({"data_fim": (date.today() - timedelta(days=1)).isoformat()})
    assert c["vencido"] and c["dias_para_vencer"] == -1


def test_existente_sem_dados_nao_quebra():
    assert _calcular({})["valor_total_calc"] is None


def test_limpar_formata_cnpj_e_ignora_campo_desconhecido():
    d = _limpar({"cnpj": "11222333000181", "hackear": 1})
    assert d == {"cnpj": "11.222.333/0001-81"}
