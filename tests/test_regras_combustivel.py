"""
Regras do combustível (app/core/fuel_calc.py e endpoints/combustivel.py) —
testes de unidade, SEM banco. Ficha: app/api/v1/endpoints/_docs/combustivel.md.

Rodar: .venv\\Scripts\\python.exe -m pytest tests/test_regras_combustivel.py -q
"""

from datetime import datetime, timedelta

from app.api.v1.endpoints.combustivel import _ciclos, _duplicados
from app.core import combustivel as plaus
from app.core import fuel_calc as F


def alertas(**kw):
    base = dict(family="diesel", km_rodados=500.0, liters=200.0, km_informado_atual=100_500.0, tank_capacity_l=300.0,
                expected_kml_max=3.0, km_por_litro=2.5, expected_kml_min=2.0, telemetry_classification="OK",
                total_value=1200.0, total_value_informed=1200.0, is_duplicate_candidate=False, reviewed=False)
    base.update(kw)
    return sorted(a.code for a in F.compute_alerts(**base))


# ------------------------------------------------------------- alertas

def test_abastecimento_normal_sem_alerta():
    assert alertas() == []


def test_km_regressivo():
    assert "KM_REGRESSIVO" in alertas(km_rodados=-10.0)


def test_acima_do_tanque_com_tolerancia_de_5_pct():
    assert "ACIMA_DO_TANQUE" not in alertas(liters=314.0)   # dentro dos 5%
    assert "ACIMA_DO_TANQUE" in alertas(liters=320.0)


def test_consumo_impossivel_diesel():
    """RCA 04/10/2026: um km digitado errado deu 130 km/L e puxou a frota para 68 km/L."""
    assert "CONSUMO_IMPOSSIVEL" in alertas(km_por_litro=130.0)
    assert "CONSUMO_IMPOSSIVEL" in alertas(km_por_litro=0.1)
    assert "CONSUMO_IMPOSSIVEL" not in alertas(km_por_litro=2.5)


def test_consumo_impossivel_sai_da_media():
    assert F.cycle_excluded_from_average(["CONSUMO_IMPOSSIVEL"], reviewed=False)
    assert not F.cycle_excluded_from_average(["CONSUMO_IMPOSSIVEL"], reviewed=True)   # verificado volta a contar
    assert not F.cycle_excluded_from_average(["CONSUMO_FORA_DA_FAIXA"], reviewed=False)


def test_arla_nao_recebe_alertas_de_consumo():
    assert alertas(family="arla", km_por_litro=130.0, liters=999.0) == []


def test_faixa_de_consumo_com_tolerancia():
    assert F.consumption_status(2.5, 2.0, 3.0) == "ok"
    assert F.consumption_status(1.9, 2.0, 3.0) == "slightly_below"
    assert F.consumption_status(1.5, 2.0, 3.0) == "below"
    assert F.consumption_status(3.2, 2.0, 3.0) == "slightly_above"
    assert F.consumption_status(None, 2.0, 3.0) == "no_data"


def test_sem_km_informado():
    assert "SEM_KM" in alertas(km_informado_atual=None)


def test_valor_divergente_acima_de_1_real():
    assert "VALOR_DIVERGENTE" not in alertas(total_value_informed=1200.80)
    assert "VALOR_DIVERGENTE" in alertas(total_value_informed=1205.0)


def test_placa_normalizada():
    assert F.normalize_plate("abc-1d23") == "ABC1D23"
    assert F.normalize_plate("OIO-1234") == "010" + "1234"


# --------------------------------------------- ciclos (view v_fuel_supply_calc)

T0 = datetime(2026, 10, 1, 8, 0)


def abast(i, horas, km, litros, cheio=True, valor=None, unit=1, family="diesel"):
    return {"id": i, "unit_id": unit, "family": family, "event_datetime": T0 + timedelta(hours=horas),
            "km_informado_atual": km, "liters": litros, "total_value": valor if valor is not None else litros * 6.0, "full_tank": cheio}


def test_ciclo_tanque_cheio_a_tanque_cheio():
    """km/L = km entre dois tanques cheios ÷ litros do ciclo (inclui o parcial do meio)."""
    linhas = [abast(1, 0, 100_000, 250), abast(2, 10, 100_300, 80, cheio=False), abast(3, 20, 100_600, 150)]
    _ciclos(linhas)
    fim = linhas[2]
    assert fim["ciclo_km"] == 600.0
    assert fim["ciclo_litros"] == 230.0            # 80 + 150
    assert fim["km_por_litro"] == round(600 / 230, 2)
    assert linhas[0]["km_por_litro"] is None       # primeiro tanque cheio não fecha ciclo


def test_custo_por_km_usa_o_valor_do_proprio_ciclo():
    """O módulo do TI dividia o gasto total pelos km válidos (deu R$ 36/km)."""
    linhas = [abast(1, 0, 100_000, 200, valor=1200), abast(2, 10, 100_500, 200, valor=1300)]
    _ciclos(linhas)
    assert linhas[1]["custo_por_km"] == round(1300 / 500, 2)


def test_ciclos_separados_por_veiculo_e_arla_fora():
    linhas = [abast(1, 0, 100_000, 200, unit=1), abast(2, 5, 50_000, 100, unit=2),
              abast(3, 10, 100_400, 160, unit=1), abast(4, 12, 0, 20, family="arla", unit=1)]
    _ciclos(linhas)
    assert linhas[2]["ciclo_km"] == 400.0
    assert linhas[3]["km_por_litro"] is None


def test_duplicado_na_janela_de_30_min():
    a = abast(1, 0, 100_000, 200)
    b = {**abast(2, 0, 100_000, 205), "event_datetime": T0 + timedelta(minutes=20)}
    c = {**abast(3, 0, 100_000, 200), "event_datetime": T0 + timedelta(minutes=50)}
    assert _duplicados([a, b]) == {1, 2}
    assert _duplicados([a, c]) == set()


# ------------------------------------------- plausibilidade (SQL compartilhado)

def test_plausibilidade_descarta_dia_impossivel():
    """Dia > 1.200 L ou > 100 L com menos de 0,5 km/L é descartado (memória do PM)."""
    sql = plaus.valido("h")
    assert "1200000" in sql and "100000" in sql and "0.5" in sql
    assert plaus.litros_ml("x").startswith("CASE WHEN (x.used_fuel_hist > 0")
