"""Regras de rota segura: geometria, programação em execução e decodificadores dos motores."""

from datetime import datetime

from app.core import geo
from app.core.areas_risco import em_execucao

QUADRADO = [[-44.0, -20.0], [-43.9, -20.0], [-43.9, -19.9], [-44.0, -19.9], [-44.0, -20.0]]  # [lng, lat]


def test_ponto_dentro_e_fora_da_area():
    assert geo.dentro(-19.95, -43.95, QUADRADO)
    assert not geo.dentro(-19.80, -43.95, QUADRADO)


def test_rota_que_atravessa_a_area_sem_vertice_dentro():
    """Os dois pontos ficam fora, mas o segmento corta a área: tem de contar."""
    linha = [[-19.95, -44.10], [-19.95, -43.80]]
    assert geo.linha_entra_na_area(linha, QUADRADO)
    assert geo.dist_area_linha_m(QUADRADO, linha) == 0


def test_rota_que_passa_ao_lado():
    linha = [[-19.80, -44.10], [-19.80, -43.80]]
    assert not geo.linha_entra_na_area(linha, QUADRADO)
    assert 10_000 < geo.dist_area_linha_m(QUADRADO, linha) < 12_000  # ~0,1° de latitude


def test_distancia_ao_segmento():
    assert round(geo.dist_ponto_linha_m(-20.0, -44.0, [[-20.0, -44.0], [-20.0, -43.0]])) == 0
    assert 1_000 < geo.dist_ponto_linha_m(-20.01, -43.5, [[-20.0, -44.0], [-20.0, -43.0]]) < 1_200


PROG = {"dias": [0, 1, 2, 3, 4], "hora_ini": "06:00", "hora_fim": "18:00"}
NOITE = {"dias": [4], "hora_ini": "22:00", "hora_fim": "06:00"}  # sexta 22h → sábado 6h


def test_programacao_no_horario():
    assert em_execucao(PROG, datetime(2026, 10, 6, 10, 0))      # terça
    assert not em_execucao(PROG, datetime(2026, 10, 6, 19, 0))  # depois do fim
    assert not em_execucao(PROG, datetime(2026, 10, 11, 10, 0))  # domingo


def test_programacao_que_cruza_a_meia_noite():
    assert em_execucao(NOITE, datetime(2026, 10, 9, 23, 0))      # sexta 23h
    assert em_execucao(NOITE, datetime(2026, 10, 10, 5, 0))      # sábado 5h, ainda a de sexta
    assert not em_execucao(NOITE, datetime(2026, 10, 10, 23, 0))  # sábado 23h


def test_decodificadores_dos_motores():
    # Exemplo da documentação do Google (precisão 5) e da HERE (flexible polyline).
    assert geo.decodificar_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", 5) == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]
    pts = geo.decodificar_flexivel("BFoz5xJ67i1B1B7PzIhaxL7Y")
    assert [round(x, 5) for x in pts[0]] == [50.10228, 8.69821]
    assert len(pts) == 4
