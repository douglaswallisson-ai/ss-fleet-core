"""
Regra única de "litros válidos" (aprovada pelo PM em 02/10/2026).

Um dia de um veículo (linha de `mova.con_driver_h_km`) só entra na soma de
litros — e no km usado para o km/l — se o combustível for possível:
- mais de 0 e até 1.200 L no dia (`used_fuel_hist` em mL): acima disso não
  cabe em tanque de ônibus ou caminhão;
- e não ter, ao mesmo tempo, mais de 100 L e menos de 0,5 km/l
  (`distance_traveled_hist` em metros ÷ mL = km/l).

Motivo: um sensor com defeito distorcia a frota inteira — o ônibus 45203 da
Fênix marcou 189 mil litros em duas semanas para 1.968 km, derrubando a média
de ~3,3 para ~1,6 km/l. A fórmula do vault (km/l = km filtrado ÷ litros,
indicadores-dashboard-start R4) continua a mesma; só a leitura impossível sai.
"""

LITROS_MAX_DIA_ML = 1_200_000
KML_MINIMO = 0.5
#: Abaixo de 0,5 km/l só é impossível com muitos litros (ver valido()).
LITROS_KML_BAIXO_ML = 100_000


def valido(a: str = "h") -> str:
    """Condição SQL: a linha tem combustível possível.

    O km/l baixo só descarta com MUITO combustível (> 100 L no dia): dia de
    motor ligado parado tem km/l baixo de verdade (marcha lenta ~3–4 L/h), e
    descartá-lo esconderia consumo real.
    """
    return (
        f"({a}.used_fuel_hist > 0 AND {a}.used_fuel_hist <= {LITROS_MAX_DIA_ML} "
        f"AND NOT ({a}.used_fuel_hist > {LITROS_KML_BAIXO_ML} AND {a}.distance_traveled_hist < {KML_MINIMO} * {a}.used_fuel_hist))"
    )


def litros_ml(a: str = "h") -> str:
    """mL válidos da linha (0 quando a leitura é impossível)."""
    return f"CASE WHEN {valido(a)} THEN {a}.used_fuel_hist ELSE 0 END"


def km_com_combustivel_m(a: str = "h") -> str:
    """Metros rodados na linha quando o combustível é válido."""
    return f"CASE WHEN {valido(a)} THEN {a}.distance_traveled_hist ELSE 0 END"
