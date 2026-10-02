"""
Rotas das páginas do Power BI "Indicadores de Condução" (Gestão de Eventos,
Análise de Condução, Central de Segurança, Gestão do Parado Ligado, Não
Identificado). Só leitura.

Fontes, as mesmas do Power BI (vault: Power-BI-Modelo-de-Dados):
- `mova.heatmap` (f_heatmap): eventos com hora e local. Tem ~269 milhões de
  linhas e só o índice (unit_id, tracker_event_id, local_time) — toda consulta
  filtra pelos três, com a lista de veículos resolvida antes. Carga com atraso
  de cerca de um dia: a rota devolve até quando há dado.
- `mova.con_stop_engine_on` (f_Stop): cada parada com motor ligado.
- `mova.con_driver_h_km` (f_historico): horas por dia, veículo e condutor.
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.gerencial import Filtros, _periodo
from app.core.database import AsyncSessionLocalReplica, get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

#: Tipos de evento → códigos de `mova.tracker_event`, nas colunas do Power BI
#: (Análise de Condução QTD). Velocidade no seco inclui L1–L3; chuva é o 37.
TIPOS_EVENTO: dict[str, list[int]] = {
    "aceleracao": [153, 305, 309],
    "freada": [9, 304, 308],
    "embreagem": [148],
    "velocidade_seco": [7, 36, 397, 398, 399, 432],
    "velocidade_chuva": [37],
    "faixa_amarela": [161],
    "faixa_vermelha": [163],
    "batendo_transmissao": [159],
    "parado_acelerando": [288],
    "sem_tracao": [13],
}
TODOS_CODIGOS = sorted({c for cs in TIPOS_EVENTO.values() for c in cs})
CASE_TIPO = "CASE " + " ".join(
    f"WHEN h.tracker_event_id IN ({', '.join(map(str, cs))}) THEN '{t}'" for t, cs in TIPOS_EVENTO.items()
) + " END"


async def _veiculos(db: AsyncSession, user, f: Filtros) -> list[int]:
    """Veículos ativos do escopo e dos filtros — a lista que alimenta o índice."""
    grupos, subgrupos = escopo_do_usuario(user)
    sql, params = clausula_escopo(grupos, subgrupos, alias="tu")
    for campo, valor in (("group_id", f.group_id), ("subgroup_id", f.subgroup_id), ("id", f.unit_id)):
        if valor is not None:
            sql += f" AND tu.{campo} = :v_{campo}"
            params[f"v_{campo}"] = valor
    rows = await db.execute(text(f"SELECT tu.id FROM mova.tracked_unit tu WHERE tu.status = 1 {sql}"), params)
    return [r[0] for r in rows.all()]


async def _ler(sql: str, params: dict) -> list:
    """Consulta numa sessão própria, para poder rodar junto de outras."""
    async with AsyncSessionLocalReplica() as s:
        return list((await s.execute(text(sql), params)).mappings().all())


def _filtro_heatmap(f: Filtros) -> tuple[str, dict]:
    if f.driver_id is None:
        return "", {}
    return " AND COALESCE(h.driver_id, 0) = :drv", {"drv": f.driver_id}


@router.get("/eventos")
async def eventos(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Totais por tipo, ranking por placa e condutor, matriz dia × hora e série diária."""
    ini, fim = _periodo(start_date, end_date, 30)
    ids = await _veiculos(db, current_user, f)
    vazio = {"inicio": ini.isoformat(), "fim": fim.isoformat(), "ultimo_evento": None, "totais": {},
             "por_placa": [], "por_condutor": [], "matriz": [], "por_dia": []}
    if not ids:
        return vazio
    extra, pextra = _filtro_heatmap(f)
    base = (
        "FROM mova.heatmap h WHERE h.unit_id = ANY(CAST(:ids AS bigint[]))"
        " AND h.tracker_event_id = ANY(CAST(:cods AS bigint[]))"
        " AND h.local_time >= :ini AND h.local_time < :fim" + extra
    )
    p = {"ids": ids, "cods": TODOS_CODIGOS, "ini": datetime.combine(ini, datetime.min.time()),
         "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time()), **pextra}

    # As três leituras no heatmap rodam ao mesmo tempo, cada uma na sua
    # conexão: em sequência, com muitos veículos, passavam de um minuto.
    por_entidade, matriz, por_dia = await asyncio.gather(
        _ler(
            f"""
                SELECT h.unit_id, COALESCE(h.driver_id, 0) AS driver_id,
                       MAX(CONCAT_WS(' · ', NULLIF(TRIM(h.label), ''), NULLIF(NULLIF(TRIM(h.label2), ''), 'NULL'))) AS placa,
                       MAX(NULLIF(NULLIF(TRIM(h.driver_name), ''), 'NULL')) AS condutor,
                       {CASE_TIPO} AS tipo, COUNT(*) AS n, MAX(h.local_time) AS ultimo
                {base}
                GROUP BY h.unit_id, COALESCE(h.driver_id, 0), tipo
                """,
            p,
        ),
        _ler(
            f"""
                SELECT EXTRACT(DOW FROM h.local_time)::int AS dow, EXTRACT(HOUR FROM h.local_time)::int AS hora,
                       {CASE_TIPO} AS tipo, COUNT(*) AS n
                {base}
                GROUP BY 1, 2, 3
                """,
            p,
        ),
        _ler(f"SELECT h.local_time::date AS dia, {CASE_TIPO} AS tipo, COUNT(*) AS n {base} GROUP BY 1, 2", p),
    )

    totais: dict[str, int] = {t: 0 for t in TIPOS_EVENTO}
    placas: dict[int, dict] = {}
    condutores: dict[int, dict] = {}
    ultimo = None
    for r in por_entidade:
        t, n = r["tipo"], int(r["n"])
        totais[t] = totais.get(t, 0) + n
        ultimo = max(ultimo, r["ultimo"]) if ultimo else r["ultimo"]
        pl = placas.setdefault(r["unit_id"], {"unit_id": r["unit_id"], "placa": r["placa"], "total": 0, **{k: 0 for k in TIPOS_EVENTO}})
        pl[t] += n
        pl["total"] += n
        c = condutores.setdefault(
            r["driver_id"],
            {"driver_id": r["driver_id"], "condutor": r["condutor"] if r["driver_id"] else "NÃO INFORMADO", "total": 0, **{k: 0 for k in TIPOS_EVENTO}},
        )
        if not c["condutor"] and r["condutor"]:
            c["condutor"] = r["condutor"]
        c[t] += n
        c["total"] += n

    dias: dict[str, dict] = {}
    for r in por_dia:
        d = dias.setdefault(r["dia"].isoformat(), {"dia": r["dia"].isoformat(), **{k: 0 for k in TIPOS_EVENTO}})
        d[r["tipo"]] += int(r["n"])

    return {
        **vazio,
        "ultimo_evento": ultimo.isoformat() if ultimo else None,
        "totais": totais,
        "total": sum(totais.values()),
        "por_placa": sorted(placas.values(), key=lambda x: -x["total"])[:50],
        "por_condutor": sorted(condutores.values(), key=lambda x: -x["total"])[:50],
        # dow: 0 = domingo, como o EXTRACT do PostgreSQL.
        "matriz": [{"dow": r["dow"], "hora": r["hora"], "tipo": r["tipo"], "n": int(r["n"])} for r in matriz],
        "por_dia": sorted(dias.values(), key=lambda x: x["dia"]),
    }


@router.get("/eventos/lista")
async def eventos_lista(
    dia: date = Query(..., description="Dia dos eventos"),
    f: Filtros = Depends(),
    limit: int = Query(1000, ge=1, le=5000),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Eventos de condução de um dia, um por linha, mais recentes primeiro.

    Fonte: `mova.heatmap`. Ele chega com dias de atraso (em 02/10/2026 estava
    em 25/09); para dia ainda não carregado, os eventos vêm do histórico de
    posições (`dev_status_30`), que é tempo real — os mesmos códigos de evento.
    """
    ids = await _veiculos(db, current_user, f)
    if not ids:
        return {"dia": dia.isoformat(), "itens": [], "ultimo_carregado": None, "fonte": "heatmap"}
    ini = datetime.combine(dia, datetime.min.time())
    fim = datetime.combine(dia + timedelta(days=1), datetime.min.time())
    # Até quando a tabela foi carregada para esses veículos.
    ultimo = (
        await db.execute(
            text(
                "SELECT MAX(h.local_time) FROM mova.heatmap h WHERE h.unit_id = ANY(CAST(:ids AS bigint[]))"
                " AND h.tracker_event_id = ANY(CAST(:cods AS bigint[])) AND h.local_time >= :desde"
            ),
            {"ids": ids, "cods": TODOS_CODIGOS, "desde": datetime.combine(dia - timedelta(days=14), datetime.min.time())},
        )
    ).scalar()
    tempo_real = ultimo is None or ultimo < fim - timedelta(minutes=5)
    extra, pextra = _filtro_heatmap(f)
    if not tempo_real:
        sql = f"""
            SELECT h.id, h.local_time, h.unit_id, h.label, h.label2, COALESCE(h.driver_id, 0) AS driver_id,
                   NULLIF(NULLIF(TRIM(h.driver_name), ''), 'NULL') AS condutor, h.tracker_event_name AS evento,
                   {CASE_TIPO} AS tipo, NULLIF(NULLIF(TRIM(h.address), ''), 'NULL') AS endereco, h.area_name AS cerca,
                   h.latitude, h.longitude, NULL::numeric AS velocidade
            FROM mova.heatmap h
            WHERE h.unit_id = ANY(CAST(:ids AS bigint[]))
              AND h.tracker_event_id = ANY(CAST(:cods AS bigint[]))
              AND h.local_time >= :ini AND h.local_time < :fim {extra}
            ORDER BY h.local_time DESC
            LIMIT :limit"""
    else:
        sql = f"""
            SELECT md5(h.unit_id::text || h.local_time::text || h.tracker_event_id::text) AS id, h.local_time, h.unit_id,
                   tu.label, tu.label2, COALESCE(h.driver_id, 0) AS driver_id,
                   NULLIF(NULLIF(TRIM(h.driver_name), ''), 'NULL') AS condutor, te.name AS evento,
                   {CASE_TIPO} AS tipo, NULLIF(NULLIF(TRIM(h.address), ''), 'NULL') AS endereco, h.area_name AS cerca,
                   h.latitude, h.longitude, h.speed AS velocidade
            FROM mova.dev_status_30 h
            JOIN mova.tracked_unit tu ON tu.id = h.unit_id
            LEFT JOIN mova.tracker_event te ON te.id = h.tracker_event_id
            WHERE h.unit_id = ANY(CAST(:ids AS bigint[]))
              AND h.tracker_event_id = ANY(CAST(:cods AS bigint[]))
              AND h.local_time >= :ini AND h.local_time < :fim {extra}
            ORDER BY h.local_time DESC
            LIMIT :limit"""
    linhas = (
        await db.execute(text(sql), {"ids": ids, "cods": TODOS_CODIGOS, "ini": ini, "fim": fim, "limit": limit, **pextra})
    ).mappings().all()
    return {
        "dia": dia.isoformat(),
        "ultimo_carregado": ultimo.isoformat() if ultimo else None,
        "fonte": "tempo_real" if tempo_real else "heatmap",
        "itens": [
            {
                "id": r["id"],
                "hora": r["local_time"].isoformat() if r["local_time"] else None,
                "unit_id": r["unit_id"],
                "placa": " · ".join(x for x in (r["label2"], r["label"]) if x and x.strip()) or str(r["unit_id"]),
                "driver_id": r["driver_id"],
                "condutor": r["condutor"],
                "evento": r["evento"],
                "tipo": r["tipo"],
                "endereco": r["endereco"],
                "cerca": r["cerca"],
                "latitude": float(r["latitude"]) if r["latitude"] is not None else None,
                "longitude": float(r["longitude"]) if r["longitude"] is not None else None,
                "velocidade": float(r["velocidade"]) if r["velocidade"] is not None else None,
            }
            for r in linhas
        ],
    }


@router.get("/parado")
async def parado(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    duracao_min: Optional[float] = Query(None, description="Minutos"),
    duracao_max: Optional[float] = Query(None, description="Minutos"),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Gestão do Parado Ligado (Power BI, f_Stop).

    Local conhecido: nome da cerca; senão o POI quando a distância está entre
    0 e 700 m; senão "NÃO CADASTRADO" (vault: indicadores-power-bi, P2).
    """
    ini, fim = _periodo(start_date, end_date, 30)
    esc, par = f.sql(current_user, "s")
    filtros_dur = ""
    if duracao_min is not None:
        filtros_dur += " AND s.total_time >= :dmin"
        par["dmin"] = duracao_min * 60
    if duracao_max is not None:
        filtros_dur += " AND s.total_time <= :dmax"
        par["dmax"] = duracao_max * 60
    base = (
        "FROM mova.con_stop_engine_on s WHERE s.start_time >= :ini AND s.start_time < :fim"
        f" {esc} {filtros_dur}"
    )
    p = {**par, "ini": datetime.combine(ini, datetime.min.time()), "fim": datetime.combine(fim + timedelta(days=1), datetime.min.time())}
    local = (
        "COALESCE(NULLIF(NULLIF(TRIM(s.fence_name), ''), 'NULL'),"
        " CASE WHEN s.poi_distance BETWEEN 0 AND 700 THEN NULLIF(NULLIF(TRIM(s.poi_name), ''), 'NULL') END, 'NÃO CADASTRADO')"
    )

    # Sete leituras independentes: juntas, cada uma na sua conexão.
    tot, por_local, por_veiculo, por_condutor, detalhe, por_hora, por_dia_mes = await asyncio.gather(
        _ler(f"SELECT COUNT(*) AS n, COALESCE(SUM(s.total_time), 0) AS seg {base}", p),
        _ler(f"SELECT {local} AS nome, SUM(s.total_time) AS seg, COUNT(*) AS n {base} GROUP BY 1 ORDER BY 2 DESC LIMIT 30", p),
        _ler(
        f"SELECT s.unit_id, MAX(s.label) AS nome, SUM(s.total_time) AS seg, COUNT(*) AS n {base} GROUP BY 1 ORDER BY 3 DESC LIMIT 30"
    , p),
        _ler(
        f"SELECT COALESCE(s.driver_id, 0) AS driver_id, COALESCE(MAX(NULLIF(NULLIF(TRIM(s.driver_name), ''), 'NULL')), 'NÃO INFORMADO') AS nome,"
        f" SUM(s.total_time) AS seg, COUNT(*) AS n {base} GROUP BY 1 ORDER BY 3 DESC LIMIT 30"
    , p),
        _ler(
        f"SELECT s.start_time, s.label, COALESCE(NULLIF(NULLIF(TRIM(s.driver_name), ''), 'NULL'), 'NÃO INFORMADO') AS condutor,"
        f" {local} AS local, s.total_time AS seg, s.latitude, s.longitude {base} ORDER BY s.total_time DESC LIMIT 100"
    , p),
        _ler(f"SELECT EXTRACT(HOUR FROM s.start_time)::int AS k, SUM(s.total_time) AS seg {base} GROUP BY 1", p),
        _ler(f"SELECT EXTRACT(DAY FROM s.start_time)::int AS k, SUM(s.total_time) AS seg {base} GROUP BY 1", p),
    )
    tot = tot[0]

    h = lambda seg: round(float(seg or 0) / 3600, 2)  # noqa: E731
    return {
        "inicio": ini.isoformat(),
        "fim": fim.isoformat(),
        "horas": h(tot["seg"]),
        "paradas": int(tot["n"]),
        "por_local": [{"nome": r["nome"], "horas": h(r["seg"]), "paradas": int(r["n"])} for r in por_local],
        "por_veiculo": [{"unit_id": r["unit_id"], "nome": r["nome"], "horas": h(r["seg"]), "paradas": int(r["n"])} for r in por_veiculo],
        "por_condutor": [{"driver_id": r["driver_id"], "nome": r["nome"], "horas": h(r["seg"]), "paradas": int(r["n"])} for r in por_condutor],
        "detalhe": [
            {
                "inicio": r["start_time"].isoformat(), "veiculo": r["label"], "condutor": r["condutor"], "local": r["local"],
                "horas": h(r["seg"]),
                "latitude": float(r["latitude"]) if r["latitude"] is not None else None,
                "longitude": float(r["longitude"]) if r["longitude"] is not None else None,
            }
            for r in detalhe
        ],
        "por_hora": [{"hora": k, "horas": h(next((r["seg"] for r in por_hora if r["k"] == k), 0))} for k in range(24)],
        "por_dia_mes": [{"dia": k, "horas": h(next((r["seg"] for r in por_dia_mes if r["k"] == k), 0))} for k in range(1, 32)],
    }


@router.get("/nao-identificado")
async def nao_identificado(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    f: Filtros = Depends(),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """Horas sem condutor identificado (driver_id 0 — o 7777 do Power BI) por dia e placa."""
    ini, fim = _periodo(start_date, end_date, 30)
    esc, par = f.sql(current_user, "h", condutor=False)
    linhas = (
        await db.execute(
            text(
                f"""
                SELECT h.dt AS dia, h.unit_id, MAX(h.label) AS placa,
                       SUM(h.time_traveled_hist) / 3600.0 AS horas,
                       SUM(CASE WHEN COALESCE(h.driver_id, 0) = 0 THEN h.time_traveled_hist ELSE 0 END) / 3600.0 AS horas_ni,
                       COUNT(*) FILTER (WHERE COALESCE(h.driver_id, 0) = 0) AS registros_ni
                FROM mova.con_driver_h_km h
                WHERE h.dt >= :ini AND h.dt <= :fim {esc}
                GROUP BY h.dt, h.unit_id
                HAVING SUM(CASE WHEN COALESCE(h.driver_id, 0) = 0 THEN h.time_traveled_hist ELSE 0 END) > 0
                ORDER BY h.dt DESC, horas_ni DESC
                """
            ),
            {**par, "ini": ini, "fim": fim},
        )
    ).mappings().all()
    itens = [
        {
            "dia": r["dia"].isoformat(), "unit_id": r["unit_id"], "placa": r["placa"],
            "horas": round(float(r["horas"] or 0), 2), "horas_ni": round(float(r["horas_ni"] or 0), 2),
            "pct": round(float(r["horas_ni"] or 0) / float(r["horas"]), 4) if r["horas"] else None,
            "registros": int(r["registros_ni"]),
        }
        for r in linhas
    ]
    return {
        "inicio": ini.isoformat(), "fim": fim.isoformat(),
        "horas_ni": round(sum(i["horas_ni"] for i in itens), 2),
        "registros": sum(i["registros"] for i in itens),
        "itens": itens,
    }
