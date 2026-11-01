"""Pneus (app/core/pneus.py) — sem banco."""

from datetime import date

from app.core import pneus as pn

HOJE = date(2026, 11, 1)


def _m(vida, entrada, saida=None, saiu="2026-01-01"):
    return {"vida": vida, "km_entrada": entrada, "km_saida": saida, "saiu_em": saiu if saida is not None else None}


def test_km_em_andamento_e_soma_de_tres_montagens():
    assert pn.resumo_km([_m(1, 100_000)], 112_500)["km_medido"] == 12_500
    r = pn.resumo_km([_m(1, 0, 12_500), _m(1, 20_000, 28_000), _m(1, 30_000, 33_200)], None)
    assert r["km_total"] == 23_700


def test_km_negativo_nao_aparece_e_e_sinalizado():
    r = pn.resumo_km([_m(1, 100_000)], 90_000)
    assert r["km_total"] == 0 and r["inconsistente"]


def test_km_informado_soma_no_total_mas_fica_separado():
    r = pn.resumo_km([_m(2, 1_000)], 1_500, 40_000)
    assert (r["km_medido"], r["km_informado"], r["km_total"]) == (500, 40_000, 40_500)


def test_km_por_vida():
    r = pn.resumo_km([_m(1, 0, 82_000), _m(2, 100_000, 155_000)], None)
    assert r["por_vida"][1]["km"] == 82_000 and r["por_vida"][2]["km"] == 55_000 and r["km_total"] == 137_000


def test_montagem_encerrada_nao_muda_com_o_km_atual():
    assert pn.resumo_km([_m(1, 0, 500)], 99_999)["km_total"] == 500


def test_cadastro_recusa_preco_e_data():
    assert pn.erro_cadastro(0, HOJE, HOJE)
    assert pn.erro_cadastro(-1, HOJE, HOJE)
    assert pn.erro_cadastro(100, date(2026, 11, 2), HOJE)
    assert pn.erro_cadastro(None, None, HOJE)
    assert pn.erro_cadastro(None, None, HOJE, carga_inicial=True) is None
    assert pn.erro_cadastro(100, HOJE, HOJE) is None


def test_quem_nao_pode_ser_montado():
    assert pn.recusa_status(pn.ESTOQUE, "montar") is None
    assert "ABC" in pn.recusa_status(pn.MONTADO, "montar", onde="ABC")
    assert "01/10" in pn.recusa_status(pn.RECAPAGEM, "montar", desde="01/10")
    assert pn.recusa_status(pn.REPARO, "montar")
    assert pn.recusa_status(pn.DESCARTADO, "montar")


def test_limite_de_recapagens_considera_a_vida_informada():
    assert pn.recapagens_feitas(2) == 1
    assert pn.limite_atingido(3, 2)
    assert not pn.limite_atingido(2, 2)


def test_posicoes_do_modelo_tem_rodado_duplo_e_estepe():
    p = pn.posicoes("2_eixos")
    assert [x["chave"] for x in p] == ["1E", "1D", "2EI", "2EX", "2DI", "2DX", "ESTEPE"]
    assert pn.rotulo_posicao("2DX") == "2º eixo direito externo"
    assert all(pn.rotulo_posicao(x["chave"]) == x["rotulo"] for m in pn.MODELOS_EIXOS for x in pn.posicoes(m))


def test_km_desatualizado_depois_de_7_dias():
    assert not pn.km_desatualizado(date(2026, 10, 25), HOJE)
    assert pn.km_desatualizado(date(2026, 10, 24), HOJE)
    assert pn.km_desatualizado(None, HOJE)


def test_km_menor_que_o_registrado_pede_confirmacao():
    assert pn.km_suspeito(900, 1000)
    assert not pn.km_suspeito(1000, 1000)
    assert not pn.km_suspeito(10, None)
