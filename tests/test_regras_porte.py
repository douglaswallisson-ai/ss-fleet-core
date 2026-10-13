"""Porte do caminhão pelo nome do modelo (app/core/porte.py) — sem banco."""

from app.core.porte import porte_do_modelo as p


def test_portes_pelo_nome():
    assert p("8.160 DRC 4X2") == "leve" and p("VW_DELIVERY_10.160") == "leve" and p("ACCELO 815 CE") == "leve"
    assert p("HR") == "leve" and p("Hyundai HR 2.5") == "leve"
    assert p("ATEGO 1719") == "medio" and p("17.190 CRM 4X2 4P") == "medio" and p("VW_CONSTELLATION_17.190") == "medio"
    assert p("24-280 CRM 6X2") == "semipesado" and p("VW 26.320 Constellation - 6x2/2026") == "semipesado"
    assert p("ATEGO 2426") == "semipesado"
    assert p("M.BENZ/AXOR 2544 S") == "pesado" and p("IVECO/STRALIS 600S44T") == "pesado" and p("VOLVO/FH 460 8X2T") == "pesado"
    assert p("DAF/XF105 FTS 460A") == "pesado" and p("G 380 A6X2") == "pesado" and p("4126 - METEOR 28.480 2025/2026") == "pesado"
    assert p("CAVALO") == "pesado"


def test_sem_classificar_e_segundo_nome():
    assert p(None) is None and p("Gol") is None and p("UVI2C18") is None
    assert p("UVI2C18", "M.BENZ/ATEGO 1719  2026") == "medio"
