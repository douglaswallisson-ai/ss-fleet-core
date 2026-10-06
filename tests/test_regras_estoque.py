"""Regras do estoque de equipamentos (endpoints/estoque.py) — sem banco. Especificação do MVP (Operações, 06/10/2026)."""

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.estoque import casar, checar_saldo, familia, mesmo_cliente, normalizar, separar_seriais, status_de


def test_normalizar_secao_5_3():
    assert normalizar(" 0010 ") == "10" and normalizar("6662.0") == "6662" and normalizar("d523") == "D523"


def test_familias_secao_5_4():
    assert familia("VIRLOC 11") == familia("VIRLOC 11 TELEMETRIA")
    assert familia("ST310") == familia("ST-340 UR") and familia("MXT 140") == familia("MXT 141")
    assert familia("VIRLOC 6") != familia("VIRLOC 8")


def eq(**kw):
    return {"modelo": "VIRLOC 6", "status": 1, "unit_id": None, "placa": None, "grupo": None, "group_id": None, "desde": None,
            "placa_anterior": None, "retirado_em": None, "placa_desativada": None, "ultima_leitura": None, **kw}


def test_mesmo_numero_em_modelos_diferentes_nao_casa():
    """6662 existe como VIRLOC 6 (Nova Macapá) e VIRLOC 11 (ANSAL)."""
    s = {"chave": "6662", "modelo": "VIRLOC 11", "serial": "6662"}
    achado, outro = casar(s, {"6662": [eq(modelo="VIRLOC 6", unit_id=1, placa="AAA-1111")]})
    assert achado is None and outro["placa"] == "AAA-1111"   # vira alerta M, não Ativo


def serial(**kw):
    return {"status_manual": None, "motivo": None, "cliente": "CLIENTE", **kw}


def test_ordem_de_status():
    instalado = eq(unit_id=1, placa="AAA-1111", grupo="CLIENTE")
    assert status_de(serial(status_manual="manutencao"), instalado)[0] == "manutencao"   # manual prevalece
    assert status_de(serial(), instalado)[0] == "ativo"
    assert status_de(serial(), eq(placa_anterior="BBB-2222", retirado_em="2026-01-01"))[:2] == ("estoque", "desinstalado")
    assert status_de(serial(), None)[0] == "estoque"
    assert status_de(serial(cliente="Estoque SS (matriz)"), None)[:2] == ("estoque", "matriz")


def test_cliente_diverge_com_siglas():
    assert mesmo_cliente("FM TRANSPORTES", "FM TRANSPORTES")
    assert mesmo_cliente("T&J LOGISTICA", "T&J LOGISTICA")
    assert mesmo_cliente("JTP", "JTP PORTO VELHO")
    assert not mesmo_cliente("SARITUR - URBANO", "NOVA MACAPA - MOBILIDADE HUMANA SPE LTDA")


def contrato(**kw):
    return {"numero": "CT-00001", "status": "ativo", "saldo_rastreadores": 2, "saldo_cameras": 5, **kw}


def test_bloqueios_da_expedicao():
    with pytest.raises(HTTPException):
        checar_saldo(contrato(status="encerrado"), "rastreador", 1)
    with pytest.raises(HTTPException):
        checar_saldo(contrato(saldo_rastreadores=0), "rastreador", 1)
    with pytest.raises(HTTPException) as e:
        checar_saldo(contrato(), "rastreador", 3)
    assert "aditivo de 1" in e.value.detail["message"]
    assert checar_saldo(contrato(), "camera", 3) is None
    assert "liberada" in checar_saldo(contrato(saldo_rastreadores=None), "rastreador", 50)


def test_seriais_colados():
    assert separar_seriais("123\n456, 789;123  0123") == ["123", "456", "789"]
