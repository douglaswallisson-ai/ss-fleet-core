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


# ------------------------------------------------------------------ aditivos

from app.api.v1.endpoints.contratos import PedidoAditivo, numero_aditivo, numero_sistema, totais_com_aditivos, validar_aditivo


def ad(**kw):
    base = {"contrato_id": 1, "tipo": "inclusao", "qtd_veiculos": 3, "valor_parcela_adicional": 390.0, "status": "ativo"}
    base.update(kw)
    return base


def test_numero_do_aditivo_amarra_no_pai():
    assert numero_sistema(42) == "CT-00042"
    assert numero_aditivo(42, 1) == "CT-00042-AD01"
    assert numero_aditivo(42, 12) == "CT-00042-AD12"


def test_mais_tres_veiculos_soma_no_contrato():
    t = totais_com_aditivos({"qtd_veiculos": 10, "valor_parcela": 1300}, [ad()])
    assert t["qtd_veiculos_total"] == 13 and t["valor_parcela_total"] == 1690 and t["aditivos_ativos"] == 1


def test_aditivo_cancelado_sai_do_total_e_retirada_subtrai():
    t = totais_com_aditivos({"qtd_veiculos": 10, "valor_parcela": 1300},
                            [ad(status="cancelado"), ad(tipo="retirada", qtd_veiculos=2, valor_parcela_adicional=260)])
    assert t["qtd_veiculos_total"] == 8 and t["valor_parcela_total"] == 1040


def test_aditivo_em_contrato_existente_sem_dados():
    """Cliente antigo sem quantidade no contrato: o aditivo conta sozinho."""
    assert totais_com_aditivos({}, [ad()])["qtd_veiculos_total"] == 3


@pytest.mark.parametrize("kw,campo", [({"qtd_veiculos": 0}, "qtd_veiculos"), ({"data_inicio": ""}, "data_inicio"),
                                      ({"data_fim": "2020-01-01"}, "data_fim"), ({"valor_parcela_adicional": -1}, "valor_parcela_adicional"),
                                      ({"tipo": "retirada", "qtd_veiculos": 20}, "qtd_veiculos")])
def test_aditivo_recusa(kw, campo):
    a = PedidoAditivo(**{"qtd_veiculos": 3, "data_inicio": "2026-10-10", **kw})
    with pytest.raises(HTTPException) as e:
        validar_aditivo(a, {}, 10)
    assert e.value.detail["field"] == campo
