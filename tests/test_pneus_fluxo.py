"""Pneus: critérios de aceite das histórias do MVP, chamando as rotas em um arquivo temporário, com veículos falsos."""

import asyncio
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import pneus as ep

HOJE = date.today()
ONTEM = HOJE - timedelta(days=1)
G = 10


class U:
    user_id, email, is_super_admin, group_access = 1, "gestor@teste", True, []


FROTA = {
    1: {"unit_id": 1, "group_id": G, "placa": "AAA1A11", "prefixo": None, "km": 112_500, "lido_em": HOJE.isoformat(), "fonte_km": "x"},
    2: {"unit_id": 2, "group_id": G, "placa": "BBB2B22", "prefixo": None, "km": 50_000, "lido_em": HOJE.isoformat(), "fonte_km": "x"},
}


@pytest.fixture(autouse=True)
def base(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "ARQUIVO", tmp_path / "p.sqlite")
    monkeypatch.setattr(ep, "_hoje", lambda: HOJE)

    async def frota(_):
        return FROTA

    async def km_data(_u, _d):
        return None

    monkeypatch.setattr(ep, "_frota", frota)
    monkeypatch.setattr(ep, "_km_na_data", km_data)


def r(c):
    return asyncio.run(c)


def novo(fogo="F1", **k):
    dados = dict(group_id=G, fogos=[fogo] if isinstance(fogo, str) else fogo, marca="Michelin", modelo="X", medida="295/80 R22.5", compra_em=ONTEM, preco=2400.0)
    return r(ep.cadastrar(ep.Cadastro(**{**dados, **k}), U()))["ids"]


def montar(i, unit=1, pos="1E", km=100_000, **k):
    return r(ep.montar(i, ep.Montar(unit_id=unit, posicao=pos, modelo_eixos="2_eixos", data=ONTEM, km=km, **k), U()))


def det(i):
    return r(ep.detalhe(i, U()))


def erro(c):
    with pytest.raises(HTTPException) as e:
        r(c)
    return e.value


def test_us01_cadastro_vira_estoque_e_vários_de_uma_vez():
    [a, b] = novo(["F1", "F2"])
    d = det(a)
    assert d["pneu"]["status"] == "estoque" and d["pneu"]["km_total"] == 0 and d["pneu"]["vida"] == 1
    assert [e["tipo"] for e in d["eventos"]] == ["compra"] and b


def test_us01_fogo_repetido_mostra_quem_usa():
    novo()
    e = erro(ep.cadastrar(ep.Cadastro(group_id=G, fogos=["f1"], marca="A", modelo="B", medida="C", compra_em=ONTEM, preco=1), U()))
    assert e.status_code == 409 and "Michelin" in e.detail


def test_us01b_carga_inicial_soma_km_medido_a_partir_da_montagem():
    [i] = novo(carga_inicial=True, vida=2, km_anterior=40_000, preco=None, compra_em=None)
    montar(i)
    p = det(i)["pneu"]
    assert (p["recapagens"], p["km_informado"], p["km_medido"], p["km_total"]) == (1, 40_000, 12_500, 52_500)
    assert not p["preco_informado"]
    assert "carga_inicial" in [e["tipo"] for e in det(i)["eventos"]]


def test_us01b_limite_avisa_e_bloqueia_novo_envio():
    r(ep.gravar_config(ep.Config(group_id=G, limite_recapagens=2), U()))
    x = r(ep.cadastrar(ep.Cadastro(group_id=G, fogos=["F9"], marca="A", modelo="B", medida="C", carga_inicial=True, vida=3), U()))
    assert "limite" in x["aviso"]
    e = erro(ep.enviar_servico(x["ids"][0], ep.EnvioServico(tipo="recapagem", data=HOJE), U()))
    assert e.status_code == 409 and "limite" in e.detail


def test_us02_posicao_ocupada_pneu_ja_montado_e_km_menor():
    [a, b] = novo(["F1", "F2"])
    montar(a)
    assert "F1" in erro(ep.montar(b, ep.Montar(unit_id=1, posicao="1E", data=HOJE, km=112_500), U())).detail["mensagem"]
    assert "AAA1A11" in erro(ep.montar(a, ep.Montar(unit_id=1, posicao="1D", data=HOJE, km=112_500), U())).detail
    e = erro(ep.montar(b, ep.Montar(unit_id=1, posicao="1D", data=HOJE, km=1), U()))
    assert e.detail["codigo"] == "km_inconsistente"
    montar(b, pos="1D", km=1, confirmar=True)
    assert det(b)["pneu"]["posicao"] == "1D"


def test_us03_km_automatico_e_total_de_montagens():
    [i] = novo()
    montar(i)
    assert det(i)["pneu"]["km_total"] == 12_500


def test_us04_ocorrencia_com_placa_custo_e_descarte():
    [i] = novo()
    montar(i)
    r(ep.ocorrencia(i, ep.Ocorrencia(tipo="Estouro", data=HOJE, km=112_500, custo=450, resultado="Segue em uso"), U()))
    d = det(i)
    o = next(e for e in d["eventos"] if e["tipo"] == "ocorrencia")
    assert o["dados"]["placa"] == "AAA1A11" and o["dados"]["posicao"] == "1E" and o["custo"] == 450
    assert d["pneu"]["custo_acumulado"] == 2850
    assert erro(ep.ocorrencia(i, ep.Ocorrencia(tipo="Outro", data=HOJE, resultado="Reparado"), U())).status_code == 422
    r(ep.ocorrencia(i, ep.Ocorrencia(tipo="Corte lateral", data=HOJE, km=112_500, resultado="Descartado"), U()))
    assert det(i)["pneu"]["status"] == "descartado" and det(i)["pneu"]["posicao"] is None


def test_us05_desmontar_grava_km_e_libera_posicao():
    [a, b] = novo(["F1", "F2"])
    montar(a)
    r(ep.desmontar(a, ep.Desmontar(data=HOJE, km=112_500, motivo="Desgaste"), U()))
    assert det(a)["pneu"]["status"] == "estoque" and det(a)["pneu"]["km_total"] == 12_500
    montar(b)


def test_us05_rodizio_troca_os_dois_no_mesmo_km():
    [a, b] = novo(["F1", "F2"])
    montar(a, pos="1E")
    montar(b, pos="1D")
    r(ep.rodizio(a, ep.Rodizio(posicao="1D", data=HOJE, km=112_500, motivo="Rodízio"), U()))
    assert det(a)["pneu"]["posicao"] == "1D" and det(b)["pneu"]["posicao"] == "1E"
    ea, eb = [next(e for e in det(x)["eventos"] if e["tipo"] == "rodizio") for x in (a, b)]
    assert ea["em"] == eb["em"] and ea["dados"]["km"] == eb["dados"]["km"]


def test_us05_mover_usa_o_km_de_cada_veiculo():
    [i] = novo()
    montar(i)
    r(ep.mover(i, ep.Mover(unit_id=2, posicao="1E", modelo_eixos="2_eixos", data=HOJE, km_saida=112_500, km_entrada=50_000, motivo="Outro"), U()))
    p = det(i)
    assert p["pneu"]["placa"] == "BBB2B22" and p["pneu"]["km_total"] == 12_500


def test_us05_desmontar_todos():
    [a, b] = novo(["F1", "F2"])
    montar(a, pos="1E")
    montar(b, pos="1D")
    assert r(ep.desmontar_todos(1, ep.DesmontarTodos(group_id=G, data=HOJE, km=112_500, motivo="Venda ou baixa do veículo"), U()))["desmontados"] == 2
    assert det(a)["pneu"]["status"] == "estoque" and det(b)["pneu"]["status"] == "estoque"


def test_us06_recapagem_aprovada_sobe_a_vida_e_soma_custo():
    [i] = novo()
    r(ep.enviar_servico(i, ep.EnvioServico(tipo="recapagem", fornecedor="Recap X", data=HOJE), U()))
    assert "fornecedor" in erro(ep.montar(i, ep.Montar(unit_id=1, posicao="1E", modelo_eixos="2_eixos", data=HOJE, km=112_500), U())).detail
    r(ep.retorno_servico(i, ep.RetornoServico(data=HOJE, custo=900, aprovado=True), U()))
    p = det(i)["pneu"]
    assert (p["vida"], p["status"], p["recapagens"], p["custo_acumulado"]) == (2, "estoque", 1, 3300)
    assert det(i)["vidas"][1]["custo"] == 900


def test_us06_reprovada_descarta_e_montado_nao_envia():
    [i] = novo()
    montar(i)
    assert erro(ep.enviar_servico(i, ep.EnvioServico(tipo="recapagem", data=HOJE), U())).status_code == 409
    r(ep.desmontar(i, ep.Desmontar(data=HOJE, km=112_500, motivo="Enviado para recapagem", destino="recapagem", fornecedor="R"), U()))
    assert det(i)["pneu"]["status"] == "recapagem"
    r(ep.retorno_servico(i, ep.RetornoServico(data=HOJE, custo=0, aprovado=False), U()))
    d = det(i)
    assert d["pneu"]["status"] == "descartado" and d["eventos"][0]["dados"]["motivo"] == "Carcaça reprovada na recapagem"


def test_reparo_volta_na_mesma_vida():
    [i] = novo()
    r(ep.enviar_servico(i, ep.EnvioServico(tipo="reparo", data=HOJE), U()))
    r(ep.retorno_servico(i, ep.RetornoServico(data=HOJE, custo=50, aprovado=True), U()))
    assert det(i)["pneu"]["vida"] == 1 and det(i)["pneu"]["status"] == "estoque" and det(i)["pneu"]["custo_acumulado"] == 2450


def test_us07_km_por_vida():
    [i] = novo()
    montar(i, km=30_500)
    r(ep.desmontar(i, ep.Desmontar(data=HOJE, km=112_500, motivo="Desgaste"), U()))
    r(ep.enviar_servico(i, ep.EnvioServico(tipo="recapagem", data=HOJE), U()))
    r(ep.retorno_servico(i, ep.RetornoServico(data=HOJE, custo=900, aprovado=True), U()))
    montar(i, km=57_500)
    d = det(i)
    assert [v["km"] for v in d["vidas"]] == [82_000, 55_000] and d["pneu"]["km_total"] == 137_000
    assert d["vidas"][1]["atual"] and d["km_medio_por_vida"] == 68_500


def test_nunca_montado_mostra_primeira_vida_com_zero():
    [i] = novo()
    assert [(v["vida"], v["km"]) for v in det(i)["vidas"]] == [(1, 0)]


def test_rn12_cliente_nao_ve_pneu_de_outra_empresa():
    [i] = novo()

    class Outro:
        user_id, email, is_super_admin, group_access = 2, "x", False, [(99, None)]

    assert erro(ep.detalhe(i, Outro())).status_code == 403


def test_data_futura_e_bloqueada():
    [i] = novo()
    amanha = HOJE + timedelta(days=1)
    assert erro(ep.montar(i, ep.Montar(unit_id=1, posicao="1E", modelo_eixos="2_eixos", data=amanha, km=112_500), U())).status_code == 422


def test_descartado_nao_monta():
    [i] = novo()
    r(ep.ocorrencia(i, ep.Ocorrencia(tipo="Estouro", data=HOJE, resultado="Descartado"), U()))
    assert "descartado" in erro(ep.montar(i, ep.Montar(unit_id=1, posicao="1E", modelo_eixos="2_eixos", data=HOJE, km=112_500), U())).detail
