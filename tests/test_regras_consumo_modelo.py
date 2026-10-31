"""Painel Consumo por Modelo (app/core/consumo_modelo.py) — sem banco."""

from datetime import date

from app.core import consumo_modelo as cm


def _col(cols, chave):
    return next(c for c in cols if c["chave"] == chave)


def test_mes_atual_compara_com_o_mesmo_periodo_do_mes_anterior():
    cols = cm.periodos(date(2026, 10, 31), date(2026, 10, 30), 6)
    a, b = _col(cols, "atual"), _col(cols, "anterior")
    assert (a["inicio"], a["fim"]) == (date(2026, 10, 1), date(2026, 10, 30))
    assert (b["inicio"], b["fim"]) == (date(2026, 9, 1), date(2026, 9, 30))
    assert a["base"] == "anterior"


def test_dia_que_nao_existe_no_mes_anterior_e_limitado():
    b = _col(cm.periodos(date(2026, 3, 31), date(2026, 3, 31), 3), "anterior")
    assert b["fim"] == date(2026, 2, 28)


def test_colunas_de_mes_e_a_base_oculta():
    cols = cm.periodos(date(2026, 1, 15), date(2026, 1, 14), 3)
    assert [c["chave"] for c in cols if c["tipo"] == "mes"] == ["2025-09", "2025-10", "2025-11", "2025-12"]
    assert cols[0]["oculta"] and _col(cols, "2025-10")["base"] == "2025-09"


def test_kml_e_soma_sobre_soma_e_nao_media_de_medias():
    linhas = [{"modelo": "A", "km": 1000, "km_comb": 1000, "litros": 100, "segundos": 3600},
              {"modelo": "A", "km": 3000, "km_comb": 3000, "litros": 1000, "segundos": 3600}]
    cel, frota = cm.agregar(linhas)
    assert cel["A"]["kml"] == 3.64  # 4000 ÷ 1100; a média das médias daria 6,5
    assert cel["A"]["veiculos"] == 2 and frota["velocidade"] == 2000.0


def test_pouco_km_e_insuficiente_e_sem_litros_nao_vira_zero():
    cel, _ = cm.agregar([{"modelo": "A", "km": 900, "km_comb": 900, "litros": 300, "segundos": 1},
                         {"modelo": None, "km": 5000, "km_comb": 0, "litros": 0, "segundos": 1}])
    assert cel["A"]["insuficiente"] and cel[cm.SEM_MODELO]["kml"] is None and not cel[cm.SEM_MODELO]["insuficiente"]


def test_qualidade_conta_cada_falha_e_o_total_sem_repetir():
    r = cm.qualidade({1, 2, 3}, {1: (1, 1, 1), 2: (0, 0, 1)}, {1, 2, 3, 4, 5}, {1, 2, 3},
                     {4: date(2026, 1, 1), 5: date(2026, 12, 1)}, date(2026, 9, 30))
    # 2 sem rpm e pedal; 3 sem nenhuma leitura de sinal; 4 offline; 5 só começou depois do período.
    assert r == {"rpm": 2, "pedal": 2, "velocidade": 1, "offline": 1, "total": 3}
