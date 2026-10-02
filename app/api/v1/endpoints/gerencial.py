"""
Séries para os relatórios gerenciais (Dashboard Start, Power BI).

Duas rotas, só leitura, sobre as tabelas consolidadas do BI:

- `/serie-diaria`: um registro por dia com km, km filtrado, litros, horas,
  eventos, horas sem condutor e as 13 faixas. O front soma, compara períodos
  e desenha a evolução — uma consulta só em vez de paginar dois relatórios.
- `/ocioso`: parado com motor ligado por veículo e por dia.

Regras (vault):
- Km filtrado = só linhas com combustível entre 0 e 500.000 mL, exclusivo
  (indicadores-dashboard-start, R1). Média km/l = km filtrado ÷ litros (R4).
- Combustível negativo vira 0 (Power BI, P2). Valores estimados (`*_estimated`)
  não entram: o Power BI não os usa, e misturá-los ao medido sem marca é o
  ponto que o vault registra como problema no Dashboard Start.
- `stop_engine_on` inclui o parado produtivo (R5/R6).
- `total_11` = as 11 faixas do Dashboard Start; `faixas_13` = as 13 do Power BI.
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

FAIXAS_SQL = """
    SUM(COALESCE(td.time_green, 0))                      AS verde,
    SUM(COALESCE(td.time_extra_eco, 0))                  AS extra_economica,
    SUM(COALESCE(td.time_inercia, 0))                    AS inercia,
    SUM(COALESCE(td.time_eco_roll, 0))                   AS eco_roll,
    SUM(COALESCE(td.time_low_speed, 0))                  AS baixa_velocidade,
    SUM(COALESCE(td.time_yellow, 0))                     AS amarela,
    SUM(COALESCE(td.time_red, 0))                        AS vermelha,
    SUM(COALESCE(td.time_blue, 0))                       AS batendo_transmissao,
    SUM(COALESCE(td.time_banguela, 0))                   AS movimento_sem_tracao,
    SUM(COALESCE(td.time_stop_accel, 0))                 AS parado_acelerando,
    SUM(COALESCE(td.time_stop_engine_on, 0))             AS parado_ocioso,
    SUM(COALESCE(td.time_stop_engine_on_productive, 0))  AS parado_produtivo,
    SUM(COALESCE(td.time_tolerancia, 0))                 AS tolerancia
"""

COLUNAS_FAIXA = [
    "verde", "extra_economica", "inercia", "eco_roll", "baixa_velocidade", "amarela", "vermelha",
    "batendo_transmissao", "movimento_sem_tracao", "parado_acelerando", "parado_ocioso",
    "parado_produtivo", "tolerancia",
]
#: As 11 do Dashboard Start: as 13 menos eco-roll e baixa velocidade.
COLUNAS_11 = [c for c in COLUNAS_FAIXA if c not in ("eco_roll", "baixa_velocidade")]


def _periodo(start_date: Optional[date], end_date: Optional[date], dias_padrao: int) -> tuple[date, date]:
    fim = end_date or (date.today() - timedelta(days=1))
    ini = start_date or (fim - timedelta(days=dias_padrao - 1))
    if fim < ini:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "end_date anterior a start_date")
    if (fim - ini).days > 366:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Período máximo de 366 dias")
    return ini, fim


class Filtros:
    """
    Filtros dos relatórios gerenciais, os mesmos do Power BI: empresa, garagem
    (subgrupo), placa e condutor. Sempre **além** do escopo do usuário.

    Instrutor e função não entram: a ligação motorista ↔ instrutor e a função
    não são expostas pelo backend.
    """

    def __init__(
        self,
        group_id: Optional[int] = Query(None),
        subgroup_id: Optional[int] = Query(None, description="Garagem"),
        unit_id: Optional[int] = Query(None, description="Placa"),
        driver_id: Optional[int] = Query(None, description="Condutor; 0 = não identificado"),
    ):
        self.group_id, self.subgroup_id, self.unit_id, self.driver_id = group_id, subgroup_id, unit_id, driver_id

    def sql(self, user, alias: str, *, unidade: bool = True, condutor: bool = True) -> tuple[str, dict]:
        grupos, subgrupos = escopo_do_usuario(user)
        sql, params = clausula_escopo(grupos, subgrupos, alias=alias)
        for campo, valor, usar in (
            ("group_id", self.group_id, True),
            ("subgroup_id", self.subgroup_id, True),
            ("unit_id", self.unit_id, unidade),
        ):
            if usar and valor is not None:
                sql += f" AND {alias}.{campo} = :f_{campo}"
                params = {**params, f"f_{campo}": valor}
        if condutor and self.driver_id is not None:
            sql += f" AND COALESCE({alias}.driver_id, 0) = :f_driver_id"
            params = {**params, "f_driver_id": self.driver_id}
        return sql, params


def _escopo(user, alias: str, group_id: Optional[int]) -> tuple[str, dict]:
    return Filtros(group_id=group_id, subgroup_id=None, unit_id=None, driver_id=None).sql(user, alias)


@router.get("/serie-diaria")
async def serie_diaria(
    start_date: Optional[date] = Query(None, description="Padrão: 30 dias até ontem"),
    end_date: Optional[date] = Query(None, description="Inclusivo. Padrão: ontem"),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    ini, fim = _periodo(start_date, end_date, 30)
    periodo = {"ini": ini, "fim": fim}

    esc_h, par_h = f.sql(current_user, "h")
    hist = {
        r["dia"]: dict(r)
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT h.dt AS dia,
                           SUM(h.distance_traveled_hist) / 1000.0 AS km,
                           SUM(CASE WHEN h.used_fuel_hist > 0 AND h.used_fuel_hist < 500000
                                    THEN h.distance_traveled_hist ELSE 0 END) / 1000.0 AS km_filtrado,
                           SUM(GREATEST(COALESCE(h.used_fuel_hist, 0), 0)) / 1000.0 AS litros,
                           SUM(h.time_traveled_hist) / 3600.0 AS horas,
                           SUM(CASE WHEN COALESCE(h.driver_id, 0) = 0 THEN h.time_traveled_hist ELSE 0 END)
                               / 3600.0 AS horas_sem_condutor,
                           SUM(COALESCE(h.count_acel_excess, 0)) AS aceleracao,
                           SUM(COALESCE(h.count_break_excess, 0)) AS freada,
                           SUM(COALESCE(h.count_clutch_excess, 0)) AS embreagem,
                           SUM(COALESCE(h.count_speed_excess, 0) + COALESCE(h.count_speed_excess_dry_l1, 0)
                             + COALESCE(h.count_speed_excess_dry_l2, 0) + COALESCE(h.count_speed_excess_dry_l3, 0))
                               AS velocidade,
                           SUM(COALESCE(h.count_speed_excess_wet_l1, 0) + COALESCE(h.count_speed_excess_wet_l2, 0)
                             + COALESCE(h.count_speed_excess_wet_l3, 0)) AS velocidade_chuva,
                           SUM(CASE WHEN h.used_fuel_hist > 0 THEN h.distance_traveled_hist ELSE 0 END) / 1000.0
                               AS km_com_combustivel,
                           COUNT(DISTINCT h.unit_id) AS veiculos,
                           COUNT(DISTINCT NULLIF(COALESCE(h.driver_id, 0), 0)) AS motoristas
                    FROM mova.con_driver_h_km h
                    WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                    GROUP BY h.dt
                    """
                ),
                {**periodo, **par_h},
            )
        ).mappings()
    }

    esc_t, par_t = f.sql(current_user, "td")
    tele = {
        r["dia"]: dict(r)
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT td.day AS dia, {FAIXAS_SQL},
                           -- Turbo negativo ou acima de 10 h vira 0 (Power BI, P2).
                           SUM(CASE WHEN td.time_over_turbo_pressure BETWEEN 0 AND 36000
                                    THEN td.time_over_turbo_pressure ELSE 0 END) AS turbo_acima,
                           SUM(CASE WHEN td.time_under_turbo_pressure BETWEEN 0 AND 36000
                                    THEN td.time_under_turbo_pressure ELSE 0 END) AS turbo_abaixo
                    FROM mova.con_telemetry_day td
                    WHERE td.day >= :ini AND td.day <= :fim {esc_t}
                    GROUP BY td.day
                    """
                ),
                {**periodo, **par_t},
            )
        ).mappings()
    }

    # Metas e pesos (Metas e Pesos / weight_range): média por faixa nos
    # subgrupos do escopo, como as medidas "Meta …" e "Ponto …" do Power BI.
    # Peso -1 vira -2 na carga do BI (P2).
    esc_w, par_w = f.sql(current_user, "w", unidade=False, condutor=False)
    mp = (
        await db.execute(
            text(
                f"""
                SELECT w.range_id, AVG(w.goal) AS meta,
                       AVG(CASE WHEN w.weight = -1 THEN -2 ELSE w.weight END) AS peso
                FROM mova.weight_range w WHERE TRUE {esc_w}
                GROUP BY w.range_id
                """
            ),
            par_w,
        )
    ).mappings().all()
    metas = {int(r["range_id"]): float(r["meta"]) for r in mp if r["meta"] is not None}
    pesos = {int(r["range_id"]): float(r["peso"]) for r in mp if r["peso"] is not None}
    meta = metas.get(0)

    # Motoristas distintos no período inteiro (a Pontuação divide o volume
    # por DISTINCTCOUNT de motoristas; somar os de cada dia contaria repetido).
    motoristas_periodo = (
        await db.execute(
            text(
                f"""
                SELECT COUNT(DISTINCT NULLIF(COALESCE(h.driver_id, 0), 0))
                FROM mova.con_driver_h_km h WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                """
            ),
            {**periodo, **par_h},
        )
    ).scalar()

    dias = []
    d = ini
    while d <= fim:
        h = hist.get(d, {})
        t = tele.get(d, {})
        faixas = {c: int(t.get(c) or 0) for c in COLUNAS_FAIXA}
        dias.append(
            {
                "dia": d.isoformat(),
                **{k: round(float(h.get(k) or 0), 3) for k in ("km", "km_filtrado", "km_com_combustivel", "litros", "horas", "horas_sem_condutor")},
                **{k: int(h.get(k) or 0) for k in ("aceleracao", "freada", "embreagem", "velocidade", "velocidade_chuva", "veiculos", "motoristas")},
                "turbo_acima": int(t.get("turbo_acima") or 0),
                "turbo_abaixo": int(t.get("turbo_abaixo") or 0),
                "faixas": faixas,
                "faixas_13": sum(faixas.values()),
                "total_11": sum(faixas[c] for c in COLUNAS_11),
                "stop_engine_on": faixas["parado_ocioso"] + faixas["parado_produtivo"],
            }
        )
        d += timedelta(days=1)

    return {
        "inicio": ini.isoformat(),
        "fim": fim.isoformat(),
        # Fração (0,15 = 15%), como o Dashboard Start lê. Sem meta: 15% (R7).
        "meta_parado": float(meta) if meta is not None else 0.15,
        "meta_parado_cadastrada": meta is not None,
        # Por range_id de mova.faixas (0 parado ligado … 23 baixa velocidade).
        "metas": metas,
        "pesos": pesos,
        "motoristas_periodo": int(motoristas_periodo or 0),
        "dias": dias,
    }


@router.get("/ocioso")
async def ocioso(
    start_date: Optional[date] = Query(None, description="Padrão: 7 dias até ontem"),
    end_date: Optional[date] = Query(None),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Parado com motor ligado (inclui produtivo) por veículo, com a série diária."""
    ini, fim = _periodo(start_date, end_date, 7)
    periodo = {"ini": ini, "fim": fim}

    esc_t, par_t = f.sql(current_user, "td")
    tele = (
        await db.execute(
            text(
                f"""
                SELECT td.unit_id, td.day AS dia,
                       SUM(COALESCE(td.time_stop_engine_on, 0) + COALESCE(td.time_stop_engine_on_productive, 0))
                           AS parado,
                       SUM(COALESCE(td.time_stop_engine_on, 0) + COALESCE(td.time_stop_engine_on_productive, 0)
                         + COALESCE(td.time_stop_accel, 0) + COALESCE(td.time_banguela, 0)
                         + COALESCE(td.time_blue, 0) + COALESCE(td.time_green, 0)
                         + COALESCE(td.time_extra_eco, 0) + COALESCE(td.time_yellow, 0)
                         + COALESCE(td.time_red, 0) + COALESCE(td.time_inercia, 0)
                         + COALESCE(td.time_tolerancia, 0)) AS total_11
                FROM mova.con_telemetry_day td
                WHERE td.day >= :ini AND td.day <= :fim {esc_t}
                GROUP BY td.unit_id, td.day
                """
            ),
            {**periodo, **par_t},
        )
    ).mappings().all()

    esc_h, par_h = f.sql(current_user, "h")
    hist = (
        await db.execute(
            text(
                f"""
                SELECT h.unit_id, COALESCE(h.driver_id, 0) AS driver_id,
                       SUM(GREATEST(COALESCE(h.used_fuel_hist, 0), 0)) / 1000.0 AS litros,
                       SUM(h.time_traveled_hist) AS segundos
                FROM mova.con_driver_h_km h
                WHERE h.dt >= :ini AND h.dt <= :fim {esc_h}
                GROUP BY h.unit_id, COALESCE(h.driver_id, 0)
                """
            ),
            {**periodo, **par_h},
        )
    ).mappings().all()

    n_dias = (fim - ini).days + 1
    por: dict[int, dict] = {}
    for r in tele:
        u = por.setdefault(r["unit_id"], {"parado": 0, "total_11": 0, "serie": [0] * n_dias, "litros": 0.0, "motoristas": {}})
        u["parado"] += int(r["parado"] or 0)
        u["total_11"] += int(r["total_11"] or 0)
        u["serie"][(r["dia"] - ini).days] += int(r["parado"] or 0)
    for r in hist:
        u = por.setdefault(r["unit_id"], {"parado": 0, "total_11": 0, "serie": [0] * n_dias, "litros": 0.0, "motoristas": {}})
        u["litros"] += float(r["litros"] or 0)
        if r["driver_id"]:
            u["motoristas"][r["driver_id"]] = u["motoristas"].get(r["driver_id"], 0) + int(r["segundos"] or 0)

    ids = list(por)
    rotulos, nomes = {}, {}
    if ids:
        rotulos = {
            r["id"]: r["rotulo"]
            for r in (
                await db.execute(
                    text(
                        "SELECT id, CONCAT_WS(' · ', NULLIF(TRIM(label2), ''), label) AS rotulo"
                        " FROM mova.tracked_unit WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )
            ).mappings()
        }
        principais = [max(u["motoristas"], key=u["motoristas"].get) for u in por.values() if u["motoristas"]]
        if principais:
            nomes = {
                r["id"]: r["name"]
                for r in (
                    await db.execute(
                        text("SELECT id, name FROM mova.driver WHERE id = ANY(:ids)"), {"ids": principais}
                    )
                ).mappings()
            }

    veiculos = []
    for uid, u in por.items():
        if u["parado"] <= 0:
            continue
        principal = max(u["motoristas"], key=u["motoristas"].get) if u["motoristas"] else None
        veiculos.append(
            {
                "unit_id": uid,
                "rotulo": rotulos.get(uid) or str(uid),
                "motorista": nomes.get(principal) if principal else None,
                "segundos_parado": u["parado"],
                "pct_parado": round(u["parado"] / u["total_11"], 4) if u["total_11"] else None,
                "litros": round(u["litros"], 1),
                "serie": u["serie"],
            }
        )
    veiculos.sort(key=lambda v: -v["segundos_parado"])
    return {"inicio": ini.isoformat(), "fim": fim.isoformat(), "veiculos": veiculos}


@router.get("/roi")
async def roi_contrato(
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    ROI e payback do contrato, para o topo da tela Início.

    Contrato: `mova.cliente_financeiro_vigencia` (parcela mensal, implantação,
    custo do litro e redução estimada, por cliente). Consumo: litros reais dos
    últimos 30 dias (`con_driver_h_km`, combustível negativo vira 0).

    Fórmula aprovada pelo produto em 01/10/2026 (não há regra no vault):
    - economia estimada no mês = litros × custo do litro × redução estimada;
    - ROI = economia ÷ parcela mensal;
    - payback (meses) = implantação ÷ (economia − parcela); sem implantação, 0.

    É estimativa a partir da meta de redução do contrato, não economia medida.
    """
    fim = date.today() - timedelta(days=1)
    ini = fim - timedelta(days=29)
    grupos, _ = escopo_do_usuario(current_user)
    alvo = [f.group_id] if f.group_id is not None else [g for g in grupos if g and g > 0]
    sql_g = "v.group_id = ANY(CAST(:grupos AS integer[]))" if alvo else "TRUE"
    contratos = (
        await db.execute(
            text(
                f"""
                SELECT v.group_id, v.data_inicio_vigencia, v.data_fim_vigencia, v.tempo_contrato_meses,
                       COALESCE(v.valor_implantacao, 0) AS implantacao, COALESCE(v.valor_parcela_mensal, 0) AS parcela,
                       v.custo_medio_combustivel_l AS custo_l, v.reducao_estimada_pct AS reducao
                FROM mova.cliente_financeiro_vigencia v
                WHERE {sql_g}
                """
            ),
            {"grupos": alvo},
        )
    ).mappings().all()
    # Só contratos do escopo do usuário (master/interno vê tudo).
    permitidos = None if getattr(current_user, "master", 0) or not grupos else set(grupos)
    contratos = [c for c in contratos if permitidos is None or c["group_id"] in permitidos]
    if not contratos:
        return {"disponivel": False, "motivo": "Sem contrato cadastrado para esta empresa."}

    esc, par = f.sql(current_user, "h")
    ids = [c["group_id"] for c in contratos]
    litros_por_grupo = {
        r[0]: float(r[1] or 0)
        for r in (
            await db.execute(
                text(
                    f"""
                    SELECT h.group_id, SUM(GREATEST(h.used_fuel_hist, 0)) / 1000.0
                    FROM mova.con_driver_h_km h
                    WHERE h.dt >= :ini AND h.dt <= :fim AND h.group_id = ANY(CAST(:ids AS integer[])) {esc}
                    GROUP BY 1
                    """
                ),
                {**par, "ini": ini, "fim": fim, "ids": ids},
            )
        ).all()
    }
    economia = parcela = implantacao = litros = 0.0
    for c in contratos:
        lt = litros_por_grupo.get(c["group_id"], 0.0)
        litros += lt
        economia += lt * float(c["custo_l"] or 0) * float(c["reducao"] or 0) / 100
        parcela += float(c["parcela"])
        implantacao += float(c["implantacao"])
    if economia <= 0 or parcela <= 0:
        return {"disponivel": False, "motivo": "Sem consumo ou sem parcela para calcular."}
    liquido = economia - parcela
    payback = 0.0 if implantacao <= 0 else (implantacao / liquido if liquido > 0 else None)
    um = contratos[0] if len(contratos) == 1 else None
    return {
        "disponivel": True,
        "inicio": ini.isoformat(),
        "fim": fim.isoformat(),
        "clientes": len(contratos),
        "litros": round(litros),
        "economia_estimada_mes": round(economia, 2),
        "parcela_mensal": round(parcela, 2),
        "implantacao": round(implantacao, 2),
        "roi": round(economia / parcela, 2),
        "payback_meses": None if payback is None else round(payback, 1),
        "reducao_estimada_pct": float(um["reducao"]) if um and um["reducao"] is not None else None,
        "custo_litro": float(um["custo_l"]) if um and um["custo_l"] is not None else None,
        "vigencia_fim": um["data_fim_vigencia"].isoformat() if um and um["data_fim_vigencia"] else None,
    }
