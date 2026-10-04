"""
Regras de cálculo do Controle de Combustível, copiadas do módulo do time de TI
(ss-fleet-core do CodeCommit, app/core/fuel_calc.py, lido em 04/10/2026).

Mantidas iguais de propósito: o PM pediu o nosso módulo "exatamente daquele
jeito". Funções puras, sem banco. A regra de alertas é a da seção 4.2 de
aux/api-combustivel-fleet-core.md (documento do time).
"""

from dataclasses import dataclass
from typing import Optional

ALERT_TYPES = [
    ("KM_REGRESSIVO", "error", "Quilometragem menor que a anterior",
     "O km informado é menor que o do abastecimento anterior. Quase sempre é erro de digitação."),
    ("ACIMA_DO_TANQUE", "error", "Litros acima da capacidade do tanque",
     "Os litros informados excedem a capacidade do tanque do veículo (não se aplica a ARLA)."),
    ("DUPLICADO", "error", "Possível abastecimento duplicado",
     "Outro abastecimento do mesmo veículo e família de combustível em até 30 minutos, com litros semelhantes."),
    ("CONSUMO_IMPOSSIVEL", "error", "Consumo impossível",
     "O km/L do ciclo não existe na prática (diesel acima de 15 ou gasolina/etanol acima de 30 km/L, ou abaixo de 0,3). Quase sempre é km digitado errado."),
    ("KM_SALTO", "warn", "Km rodados maior que o esperado",
     "Os km rodados desde o último abastecimento são maiores do que um tanque cheio permitiria."),
    ("CONSUMO_FORA_DA_FAIXA", "warn", "Consumo fora da faixa esperada",
     "O km/L deste ciclo está fora da faixa de consumo esperada para o veículo."),
    ("TELEMETRIA_DIVERGENTE", "warn", "Divergência com o rastreador",
     "A diferença entre o km informado e o km do rastreador passa dos limites configurados."),
    ("FORA_DO_POSTO", "warn", "Veículo fora do posto informado",
     "O rastreador indica o veículo longe do posto na hora do abastecimento."),
    ("VALOR_DIVERGENTE", "warn", "Valor da nota diverge do calculado",
     "O valor informado da nota difere do cálculo (litros x preço por litro)."),
    ("SEM_KM", "warn", "Sem quilometragem informada",
     "O abastecimento não tem km informado e fica fora do cálculo de consumo."),
    ("SEM_TELEMETRIA", "info", "Sem dados do rastreador",
     "O rastreador não tem leituras próximas ao horário do abastecimento."),
]
ALERT_LABELS = {code: label for code, _s, label, _d in ALERT_TYPES}

PENDING_ERROR_CODES = [
    ("PLACA_SEM_VEICULO", "Placa sem veículo"),
    ("PLACA_GENERICA", "Placa genérica"),
    ("PRODUTO_DESCONHECIDO", "Produto desconhecido"),
    ("DADOS_INCOMPLETOS", "Dados incompletos"),
    ("DUPLICADO_NA_ORIGEM", "Duplicado na origem"),
    ("OUTRO", "Outro"),
]

DEFAULT_THRESHOLDS = {
    "divergence_pct": 10,
    "divergence_min_km": 30,
    "duplicate_window_min": 30,
    "tank_tolerance_pct": 5,
    "consumption_tolerance_pct": 10,
    "station_extra_radius_m": 200,
    "invoice_tolerance_brl": 1.0,
}

# Tolerância fixa (km) da classificação OK|DIVERGENTE da telemetria.
TELEMETRY_CLASSIFICATION_TOLERANCE_KM = 5

# Ciclos com estes códigos (não verificados) ficam fora da média do período.
CYCLE_EXCLUDING_CODES = {"KM_SALTO", "KM_REGRESSIVO", "ACIMA_DO_TANQUE", "DUPLICADO", "CONSUMO_IMPOSSIVEL"}

# Acréscimo nosso (não existe no módulo do time): sem perfil de consumo, o
# KM_SALTO não dispara e um km digitado errado entrava na média — na RCA, um
# caminhão saiu com 130 km/L e a frota com 68 km/L (04/10/2026).
# SUPOSIÇÃO: limites físicos por família; confirmar com o time de TI.
KML_IMPOSSIVEL = {"diesel": (0.3, 15.0), "otto": (0.3, 30.0)}


@dataclass
class Alert:
    code: str
    severity: str  # error|warn|info
    reviewed: bool = False


def classify_fuel_family(fuel_type_code: Optional[str]) -> str:
    """Diesel S10/S500 -> diesel; Gasolina/Etanol -> otto; resto -> arla."""
    if fuel_type_code in ("DIESEL_S10", "DIESEL_S500"):
        return "diesel"
    if fuel_type_code in ("GASOLINA", "ETANOL"):
        return "otto"
    return "arla"


def consumption_status(km_per_liter, expected_min, expected_max,
                       tolerance_pct: float = DEFAULT_THRESHOLDS["consumption_tolerance_pct"]) -> str:
    """ok | slightly_below | slightly_above | below | above | no_data."""
    if km_per_liter is None or expected_min is None or expected_max is None:
        return "no_data"
    if expected_min <= km_per_liter <= expected_max:
        return "ok"
    tolerance_min = expected_min * (1 - tolerance_pct / 100)
    tolerance_max = expected_max * (1 + tolerance_pct / 100)
    if km_per_liter < expected_min:
        return "slightly_below" if km_per_liter >= tolerance_min else "below"
    return "slightly_above" if km_per_liter <= tolerance_max else "above"


def compute_alerts(*, family, km_rodados, liters, km_informado_atual, tank_capacity_l, expected_kml_max,
                   km_por_litro, expected_kml_min, telemetry_classification, total_value,
                   total_value_informed, is_duplicate_candidate, reviewed,
                   thresholds: dict = DEFAULT_THRESHOLDS) -> list[Alert]:
    alerts: list[Alert] = []
    is_arla = family == "arla"

    if km_rodados is not None and km_rodados < 0:
        alerts.append(Alert("KM_REGRESSIVO", "error", reviewed))
    if not is_arla and liters is not None and tank_capacity_l is not None:
        if liters > tank_capacity_l * (1 + thresholds["tank_tolerance_pct"] / 100):
            alerts.append(Alert("ACIMA_DO_TANQUE", "error", reviewed))
    if is_duplicate_candidate:
        alerts.append(Alert("DUPLICADO", "error", reviewed))
    if (not is_arla and km_rodados is not None and tank_capacity_l is not None and expected_kml_max is not None
            and km_rodados > tank_capacity_l * expected_kml_max * 1.5):
        alerts.append(Alert("KM_SALTO", "warn", reviewed))
    if not is_arla and km_por_litro is not None and family in KML_IMPOSSIVEL:
        lo, hi = KML_IMPOSSIVEL[family]
        if km_por_litro < lo or km_por_litro > hi:
            alerts.append(Alert("CONSUMO_IMPOSSIVEL", "error", reviewed))
    if not is_arla:
        st = consumption_status(km_por_litro, expected_kml_min, expected_kml_max, thresholds["consumption_tolerance_pct"])
        if st in ("below", "above"):
            alerts.append(Alert("CONSUMO_FORA_DA_FAIXA", "warn", reviewed))
    if telemetry_classification == "DIVERGENTE":
        alerts.append(Alert("TELEMETRIA_DIVERGENTE", "warn", reviewed))
    elif telemetry_classification == "SEM_TELEMETRIA":
        alerts.append(Alert("SEM_TELEMETRIA", "info", reviewed))
    if total_value_informed is not None and total_value is not None:
        if abs(float(total_value_informed) - float(total_value)) > thresholds["invoice_tolerance_brl"]:
            alerts.append(Alert("VALOR_DIVERGENTE", "warn", reviewed))
    if km_informado_atual is None:
        alerts.append(Alert("SEM_KM", "warn", reviewed))
    return alerts


def cycle_excluded_from_average(alert_codes: list[str], reviewed: bool) -> bool:
    if reviewed:
        return False
    return any(code in CYCLE_EXCLUDING_CODES for code in alert_codes)


def normalize_plate(plate: str) -> str:
    """Maiúsculas, sem pontuação, O->0 e I->1."""
    cleaned = "".join(ch for ch in (plate or "").upper() if ch.isalnum())
    return cleaned.replace("O", "0").replace("I", "1")


def levenshtein_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
