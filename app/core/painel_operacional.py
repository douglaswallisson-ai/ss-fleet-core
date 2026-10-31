"""Painel Operacional do CCO (pedido do PM, 31/10/2026): regras sem banco.

- km/l do período = SOMA(km) ÷ SOMA(litros), nunca média de médias; km e litros com a regra de
  plausibilidade aprovada (`app/core/combustivel.py`).
- Modelo com menos de KM_MINIMO km com combustível no período: "dados insuficientes".
- Mês atual: acumulado do dia 1 até o último dia com dado, comparado com o mesmo período do mês anterior.
- Falha de dados de telemetria: o veículo rodou mais de 1 km no período e nenhuma leitura veio com
  RPM, pedal ou velocidade do CAN acima de zero. Offline: nenhuma leitura no último dia do período.
"""

import calendar
from datetime import date, timedelta
from typing import Optional

#: Abaixo disso, com combustível válido no período, o km/l do modelo não é mostrado.
KM_MINIMO = 1000.0
#: Na coluna de hoje o dia ainda está correndo: de manhã quase nenhum modelo teria 1.000 km.
KM_MINIMO_HOJE = 200.0
#: Viagem fora desta faixa de km/l tem leitura de combustível impossível (sem o filtro, a frota dava 0,07 km/l em
#: 30/10; com ele, 2,54 em 29/10 contra 2,52 do consolidado). Mesma faixa do comparativo de hoje do CCO.
KML_VIAGEM_MIN, KML_VIAGEM_MAX = 0.8, 8.0
#: Dia com menos que esta fração dos veículos dos dias anteriores ainda está sendo consolidado (30/10: 81 contra ~1.780).
FRACAO_DIA_COMPLETO = 0.5
#: Veículo só é avaliado na qualidade do dado se rodou mais que isso no período.
KM_MIN_QUALIDADE = 1.0
SEM_MODELO = "Sem modelo cadastrado"
NOMES_MES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _mes_antes(ano: int, mes: int, n: int = 1) -> tuple[int, int]:
    total = ano * 12 + (mes - 1) - n
    return total // 12, total % 12 + 1


def _fim_mes(ano: int, mes: int) -> date:
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


def _coluna_mes(ano: int, mes: int, oculta: bool = False) -> dict:
    return {"chave": f"{ano}-{mes:02d}", "rotulo": f"{NOMES_MES[mes - 1]}/{str(ano)[2:]}", "tipo": "mes",
            "inicio": date(ano, mes, 1), "fim": _fim_mes(ano, mes), "oculta": oculta}


def periodos(hoje: date, ultimo_dado: Optional[date], meses: int) -> list[dict]:
    """Colunas do painel. A primeira, oculta, só serve de base da variação do primeiro mês mostrado.

    `ultimo_dado` é o último dia já consolidado: o acumulado vai até ele, para os dois lados da
    comparação terem os mesmos dias.
    """
    ref = min(hoje, ultimo_dado) if ultimo_dado else hoje
    ano, mes = hoje.year, hoje.month
    dia = ref.day if (ref.year, ref.month) == (ano, mes) else 1
    fechados = [_coluna_mes(*_mes_antes(ano, mes, n), oculta=n == meses + 1) for n in range(meses + 1, 0, -1)]
    for i in range(1, len(fechados)):
        fechados[i]["base"] = fechados[i - 1]["chave"]
    ap, mp = _mes_antes(ano, mes)
    fim_ant = date(ap, mp, min(dia, calendar.monthrange(ap, mp)[1]))
    atual = {"chave": "atual", "rotulo": f"Mês atual (até {dia:02d}/{mes:02d})", "tipo": "atual",
             "inicio": date(ano, mes, 1), "fim": date(ano, mes, dia), "oculta": False, "base": "anterior"}
    anterior = {"chave": "anterior", "rotulo": f"Mesmo período de {NOMES_MES[mp - 1]} (até {fim_ant.day:02d}/{mp:02d})",
                "tipo": "anterior", "inicio": date(ap, mp, 1), "fim": fim_ant,
                # No último dia do mês anterior, é o mês inteiro: a coluna repetiria a do mês (PM, 31/10/2026).
                # Continua sendo a base da variação do mês atual, só não aparece.
                "oculta": fim_ant == _fim_mes(ap, mp)}
    return [*fechados, atual, anterior]


def dias_do_historico(hoje: date, meses: int) -> list[date]:
    """Dias já encerrados que a qualidade do dado precisa, do mais recente para o mais antigo."""
    a, m = _mes_antes(hoje.year, hoje.month, meses + 1)
    d, ini, out = hoje - timedelta(days=1), date(a, m, 1), []
    while d >= ini:
        out.append(d)
        d -= timedelta(days=1)
    return out


def ultimo_dia_completo(contagem: dict[date, int]) -> Optional[date]:
    """Último dia consolidado de verdade: tem ao menos metade dos veículos dos 7 dias anteriores com dado."""
    dias = sorted(contagem)
    for i in range(len(dias) - 1, -1, -1):
        antes = sorted(contagem[d] for d in dias[max(0, i - 7):i])
        if not antes or contagem[dias[i]] >= antes[len(antes) // 2] * FRACAO_DIA_COMPLETO:
            return dias[i]
    return None


def _celula(km: float, km_comb: float, litros: float, veiculos: int, km_minimo: float = KM_MINIMO) -> dict:
    """km/l só com km e litros das mesmas linhas válidas; sem litros, None ("—"), nunca 0."""
    base = {"km": round(km), "km_comb": round(km_comb), "litros": round(litros), "veiculos": veiculos}
    if km_comb <= 0 or litros <= 0:
        return {**base, "kml": None, "insuficiente": False}
    return {**base, "kml": round(km_comb / litros, 2), "insuficiente": km_comb < km_minimo}


def agregar(linhas: list[dict], km_minimo: float = KM_MINIMO) -> tuple[dict[str, dict], dict]:
    """Linhas por veículo (modelo, km, km_comb, litros, segundos) → células por modelo e da frota."""
    soma: dict[str, list] = {}
    km, km_comb, litros, segundos, veiculos = 0.0, 0.0, 0.0, 0.0, 0
    for r in linhas:
        s = soma.setdefault(r["modelo"] or SEM_MODELO, [0.0, 0.0, 0.0, 0])
        rodou = 1 if r["km"] > 0 else 0
        s[0] += r["km"]
        s[1] += r["km_comb"]
        s[2] += r["litros"]
        s[3] += rodou
        km += r["km"]
        km_comb += r["km_comb"]
        litros += r["litros"]
        segundos += r["segundos"]
        veiculos += rodou
    celulas = {m: _celula(*v, km_minimo) for m, v in soma.items()}
    frota = _celula(km, km_comb, litros, veiculos, km_minimo)
    frota["velocidade"] = round(km / (segundos / 3600), 1) if segundos > 0 else None
    return celulas, frota


def altitude_media(por_unidade: dict[int, tuple[float, int]]) -> Optional[int]:
    """Soma das altitudes ÷ número de leituras. Sem leitura nenhuma, None ("—"), nunca 0."""
    n = sum(x[1] for x in por_unidade.values())
    return round(sum(x[0] for x in por_unidade.values()) / n) if n else None


def qualidade(rodaram: set[int], sinais: dict[int, tuple[int, int, int]], ativos: set[int],
              com_leitura_no_fim: set[int], primeira_leitura: dict[int, date], fim: date) -> dict[str, int]:
    """Veículos por tipo de falha no período. `sinais`: unidade → (tem_rpm, tem_pedal, tem_velocidade)."""
    sem = (0, 0, 0)
    rpm = {u for u in rodaram if not sinais.get(u, sem)[0]}
    pedal = {u for u in rodaram if not sinais.get(u, sem)[1]}
    vel = {u for u in rodaram if not sinais.get(u, sem)[2]}
    # Só conta quem já comunicou e parou. Instalado depois do fim do período ainda não existia; "ativo" sem nenhuma
    # leitura no histórico é cadastro antigo, não falha de telemetria (eram ~1.500 de ~3.400 na conta inicial).
    off = {u for u in ativos if u not in com_leitura_no_fim and u in primeira_leitura and primeira_leitura[u] <= fim}
    return {"rpm": len(rpm), "pedal": len(pedal), "velocidade": len(vel), "offline": len(off), "total": len(rpm | pedal | vel | off)}
