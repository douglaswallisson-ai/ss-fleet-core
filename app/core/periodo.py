"""
Limites de data para as tabelas divididas por dia ou mês (`dev_status_30`,
`con_telemetry`, `con_stop`).

As partes são do modelo antigo (herança com CHECK de data). O banco só deixa de
abrir uma parte quando a data chega PRONTA na consulta — com `now() - interval`
ele abre todas: em 11/10/2026, 40 s contra 0,02 s na mesma consulta. E toda
consulta precisa de limite final: há partes criadas até 2093, algumas com dado de
relógio errado (con_telemetry de set/2045 com 807 viagens), que entravam como
"recentes" e faziam o sinótico levar 22 s.

Uso: `... AND d.local_time >= :desde AND d.local_time < :ate_limite`, com
`ate_limite()` no dicionário de parâmetros (as funções `_ler` já põem).
"""

from datetime import datetime, timedelta

#: Folga para relógio de rastreador um pouco adiantado.
FOLGA = timedelta(hours=1)


def ate_limite() -> datetime:
    return datetime.now() + FOLGA


def com_limite(p: dict) -> dict:
    """Parâmetros com `ate_limite` (o valor passado pela consulta, se houver, vale mais)."""
    return {"ate_limite": ate_limite(), **p}
