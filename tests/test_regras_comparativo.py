"""Regras do comparativo com iguais (endpoints/comparativo.py) — sem banco."""

from app.api.v1.endpoints.comparativo import comparar, indicadores_do_cliente, segmento


def _k(**kw):
    base = {"veiculos": 10, "km": 100_000, "km_comb": 90_000, "litros": 30_000, "eventos": 500, "velocidade": 300, "km_sem": 5_000}
    return {**base, **kw}


def test_segmento():
    assert segmento(True, 0) == "urbano"
    assert segmento(False, 5) == "fretamento"
    assert segmento(False, 4) == "carga"
    assert segmento(False, 0, None, maquinas=26, ativos=57) == "maquinas"
    assert segmento(False, 0, {"urbano": True}) == "urbano"           # ajuste da SS vale mais
    assert segmento(True, 50, {"urbano": False}) == "fretamento"


def test_quem_nao_mede_nao_entra():
    v = indicadores_do_cliente(_k(eventos=0, litros=0), {"parado": 10, "total": 100}, 30)
    assert v["eventos100"] is None          # zero = o equipamento não conta
    assert v["kml"] is None
    assert indicadores_do_cliente(_k(km_comb=40_000), None, 30)["kml"] is None   # combustível cobre < 50% dos km
    assert indicadores_do_cliente(_k(eventos=60_000), None, 30)["eventos100"] is None  # > 0,5 por km
    v = indicadores_do_cliente(_k(), {"parado": 25, "total": 100}, 30)
    assert v["kml"] == 3.0 and v["ocioso_pct"] == 25.0 and v["sem_motorista_pct"] == 5.0
    assert v["km_veiculo_dia"] == round(100_000 / 10 / 30, 1)


def test_posicao_entre_iguais():
    iguais = [(f"C{i}", float(v)) for i, v in enumerate([10, 20, 30, 40, 50])]
    r = comparar(5.0, iguais, menor_melhor=True)
    assert r["situacao"] == "melhor" and r["posicao"] == 1 and r["de"] == 6 and r["melhor_que_pct"] == 100
    assert comparar(60.0, iguais, menor_melhor=True)["situacao"] == "pior"
    assert comparar(30.0, iguais, menor_melhor=True)["situacao"] == "media"
    assert comparar(60.0, iguais, menor_melhor=False)["situacao"] == "melhor"
    assert comparar(None, iguais, True)["situacao"] == "sem_dado"
    assert comparar(1.0, iguais[:2], True)["situacao"] == "poucos_iguais"
