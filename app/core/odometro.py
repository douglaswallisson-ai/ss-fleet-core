"""Odômetro e horímetro atuais: regra única da plataforma.

Antes eram três leituras para o mesmo número: Veículos usava `odom_total`,
Manutenção usava `odom` (caindo para o CAN) e a preventiva usava o fim da
última viagem. Na CECOTI (out/2026) as três divergiam em 5 de 132 veículos.

Odômetro (metros em `mova.dev_status`):
    odom_total → odom → can_total_odometer, o primeiro válido.
    É a regra do plataforma_web (`coalesce(odom_total, odom)`), a que o cliente
    vê em produção, com a trava de leitura inválida do vault
    (dev_status-estado-atual-e-qualidade-do-odometro): 0 e 100.000.000 são
    valores sentinela do equipamento, nunca quilometragem.

Horímetro: `can_engine_hourmeter` → `hourmeter`, em MINUTOS (PM, 02/10/2026).
`hourmeter_total` repete o `hourmeter`.
"""

from typing import Optional

ODOM_INVALIDOS = {0, 100_000_000}


def odometro_sql(a: str = "ds") -> str:
    """Expressão SQL do odômetro atual em metros."""
    v = lambda c: f"CASE WHEN {a}.{c} IS NOT NULL AND {a}.{c} NOT IN (0, 100000000) THEN {a}.{c} END"  # noqa: E731
    return f"COALESCE({v('odom_total')}, {v('odom')}, {v('can_total_odometer')})"


def odometro_km(odom_total, odom, can_total_odometer) -> Optional[float]:
    for x in (odom_total, odom, can_total_odometer):
        if x is not None and int(x) not in ODOM_INVALIDOS and float(x) > 0:
            return float(x) / 1000
    return None


def horimetro_h(can_engine_hourmeter, hourmeter) -> Optional[float]:
    minutos = next((float(x) for x in (can_engine_hourmeter, hourmeter) if x is not None and float(x) > 0), None)
    return minutos / 60 if minutos else None
