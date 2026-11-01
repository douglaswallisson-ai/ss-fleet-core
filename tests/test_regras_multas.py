"""Quadro de multas (app/core/multas.py) — sem banco."""

from datetime import date

from app.core import multas as mu


def _reg(status="autuacao", ait="A1"):
    return mu.RegistroOficial(ait=ait, placa="ABC1D23", status=status, uf="MG", autuada_em="2026-10-01T10:00:00")


def test_status_desconhecido_e_recusado():
    assert mu.validar(_reg("em_analise"))
    assert mu.validar(_reg(ait=" "))
    assert mu.validar(_reg()) is None


def test_recorrida_vale_ate_o_orgao_mudar_o_status():
    assert mu.coluna_ao_sincronizar("recorrida", "infracao", "infracao") == "recorrida"
    assert mu.coluna_ao_sincronizar("recorrida", "infracao", "paga") == "paga"
    assert mu.coluna_ao_sincronizar("recorrida", "autuacao", "infracao") == "infracao"
    assert mu.coluna_ao_sincronizar(None, None, "autuacao") == "autuacao"


def test_gestor_so_move_para_etapa_interna():
    assert mu.recusa_movimento("infracao", "recorrida") is None
    assert mu.recusa_movimento("autuacao", "recorrida") is None
    assert mu.recusa_movimento("infracao", "paga") == mu.AVISO_SO_OFICIAL
    assert mu.recusa_movimento("recorrida", "cancelada") == mu.AVISO_SO_OFICIAL
    assert mu.recusa_movimento("recorrida", "infracao")
    assert mu.recusa_movimento("paga", "recorrida")
    assert mu.recusa_movimento("recorrida", "recorrida")


def test_destaque_ate_7_dias_e_nunca_em_coluna_final():
    hoje = date(2026, 11, 1)
    assert mu.prazo_em_destaque("2026-11-08", "infracao", hoje)
    assert not mu.prazo_em_destaque("2026-11-09", "infracao", hoje)
    assert mu.prazo_em_destaque("2026-10-20", "autuacao", hoje)
    assert not mu.prazo_em_destaque("2026-11-02", "paga", hoje)
    assert not mu.prazo_em_destaque(None, "infracao", hoje)


def test_coluna_soma_e_ordena_pelo_prazo_mais_proximo():
    multas = [
        {"id": 1, "coluna": "infracao", "valor": 100.5, "prazo_recurso": None, "prazo_pagamento": None},
        {"id": 2, "coluna": "infracao", "valor": None, "prazo_recurso": "2026-12-01", "prazo_pagamento": "2026-11-20"},
        {"id": 3, "coluna": "infracao", "valor": 50, "prazo_recurso": "2026-11-25", "prazo_pagamento": None},
    ]
    col = next(c for c in mu.montar_quadro(multas) if c["chave"] == "infracao")
    assert [c["id"] for c in col["cards"]] == [2, 3, 1]
    assert col["quantidade"] == 3 and col["valor_total"] == 150.5
    assert [c["chave"] for c in mu.montar_quadro([])] == list(mu.COLUNAS)
