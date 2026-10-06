"""
Events endpoints — alarmes e ocorrências.

A tabela `fleet_events` existe no modelo, mas está vazia em produção
(06/10/2026): os disparos reais ficam em `mova.alarm_violation`, que é o que
este recurso lê. A API não grava nada nessa tabela.

O que diferencia este recurso de um relatório: evento tem **estado**. Ele é
reconhecido ou não, por alguém, em algum momento. Sem esse ciclo, alarme vira
lista que ninguém trata — e uma lista que ninguém trata deixa de ser lida.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.logging import get_logger
from app.middleware.auth import require_permission
from app.models.user import User
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.schemas.events import (
    AlarmesNaoVisualizados,
    EventAcknowledge,
    EventListResponse,
    EventResponse,
    EventSummary,
)

logger = get_logger(__name__)
router = APIRouter()


#: `alarm.level` → severidade da resposta (mesmo vocabulário do enum antigo).
#: 3 é o nível que toca o som no Monitor de Alarmes.
SEVERIDADE_SQL = (
    "CASE COALESCE(al.level, 1) WHEN 3 THEN 'CRITICAL' WHEN 2 THEN 'WARNING' ELSE 'INFO' END"
)
#: Tratado no Monitor: alguém marcou como visto ou registrou a tratativa.
TRATADO_SQL = "(COALESCE(av.user_view, 0) > 0 OR av.date_modified IS NOT NULL)"


def _coord(v) -> Optional[float]:
    """`lat`/`lon` são texto em `alarm_violation`; vazio ou lixo vira nulo."""
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@router.get("/", response_model=EventListResponse)
async def list_events(
    start_date: Optional[datetime] = Query(None, description="Padrão: 24 h atrás"),
    end_date: Optional[datetime] = Query(None),
    vehicle_id: Optional[int] = Query(None),
    severity: Optional[str] = Query(None, description="info, warning, critical"),
    event_type: Optional[str] = Query(None, description="Nome do alarme"),
    only_pending: bool = Query(False, description="Somente não tratados"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Disparos de alarme (`alarm_violation`), com o resumo por severidade junto.

    Antes lia `fleet_events`, que está vazia em produção (06/10/2026). O
    formato da resposta foi mantido: `event_type` é o nome do alarme,
    `severity` sai do nível (3 crítico, 2 alerta, 1 informativo) e
    "reconhecido" é o que a plataforma atual registra — visto (`user_view`) ou
    tratado (`user_modified`/`date_modified`). A API não grava essa marcação.

    Diferente de `/alarmes`, que reproduz o Monitor (só alarmes ativos e
    exibidos no monitor), aqui entram todos os disparos do intervalo.

    O resumo vem na mesma resposta de propósito: a tela precisa dos contadores
    para os cartões do topo, e uma segunda chamada só para contar dobraria a
    carga de uma consulta que já varre o mesmo intervalo.
    """
    # Sem intervalo, as últimas 24 h. Abrir a tela e ver a tabela inteira seria
    # lento e inútil — evento antigo se consulta com filtro, não por acidente.
    if not start_date:
        start_date = datetime.now() - timedelta(hours=24)
    if not end_date:
        end_date = datetime.now()

    where = ["av.initial_time >= :start", "av.initial_time <= :end"]
    params: dict = {"start": start_date, "end": end_date, "limit": limit, "offset": offset}

    if vehicle_id is not None:
        where.append("av.unit_id = :vehicle_id")
        params["vehicle_id"] = vehicle_id
    if severity:
        # A tela manda minúsculo; a resposta usa o vocabulário do enum antigo.
        sev = severity.strip().upper()
        if sev not in ("INFO", "WARNING", "CRITICAL"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "severity deve ser info, warning ou critical")
        where.append(f"{SEVERIDADE_SQL} = :severity")
        params["severity"] = sev
    if event_type:
        where.append("al.name = :event_type")
        params["event_type"] = event_type
    if only_pending:
        where.append(f"NOT {TRATADO_SQL}")

    # Escopo pelo veículo do disparo. Sem isso, qualquer usuário autenticado
    # leria os alarmes de todos os clientes.
    grupos, subgrupos = escopo_do_usuario(current_user)
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="tu")
    params.update(escopo_params)

    clausula = " AND ".join(where) + escopo_sql
    origem = """
        FROM mova.alarm_violation av
        JOIN mova.alarm al ON al.id = av.alarm_id
        JOIN mova.tracked_unit tu ON tu.id = av.unit_id
    """

    rows = (
        await db.execute(
            text(
                f"""
                SELECT
                    av.id, av.unit_id AS vehicle_id, al.name AS event_type,
                    {SEVERIDADE_SQL} AS severity, av.initial_time AS timestamp,
                    av.final_time, al.level, av.status, av.speed, av.lat, av.lon,
                    NULLIF(TRIM(av.address), '') AS address,
                    NULLIF(TRIM(av.driver_name), '') AS driver_name,
                    NULLIF(TRIM(av.area_name), '') AS area_name,
                    NULLIF(TRIM(av.obs_modified), '') AS observacao,
                    COALESCE(av.user_view, 0) AS user_view,
                    {TRATADO_SQL} AS acknowledged,
                    av.user_modified AS acknowledged_by, av.date_modified AS acknowledged_at,
                    tu.label  AS vehicle_label,
                    tu.label2 AS vehicle_prefix,
                    COUNT(*) OVER () AS total_count
                {origem}
                WHERE {clausula}
                ORDER BY av.initial_time DESC, av.id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()

    resumo = (
        await db.execute(
            text(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE {SEVERIDADE_SQL} = 'CRITICAL') AS critical,
                    COUNT(*) FILTER (WHERE {SEVERIDADE_SQL} = 'WARNING')  AS warning,
                    COUNT(*) FILTER (WHERE {SEVERIDADE_SQL} = 'INFO')     AS info,
                    COUNT(*) FILTER (WHERE NOT {TRATADO_SQL})             AS pending,
                    COUNT(DISTINCT av.unit_id)                             AS vehicles
                {origem}
                WHERE {clausula}
                """
            ),
            {k: v for k, v in params.items() if k not in ("limit", "offset")},
        )
    ).mappings().first()

    return EventListResponse(
        items=[
            EventResponse(
                id=r["id"],
                vehicle_id=r["vehicle_id"],
                vehicle_label=r["vehicle_label"],
                vehicle_prefix=r["vehicle_prefix"],
                event_type=r["event_type"],
                severity=r["severity"],
                timestamp=r["timestamp"],
                description=r["address"],
                latitude=_coord(r["lat"]),
                longitude=_coord(r["lon"]),
                data={
                    "fonte": "alarm_violation",
                    "nivel": r["level"],
                    "fim": r["final_time"].isoformat() if r["final_time"] else None,
                    # 1 aberto, 3 encerrado (plataforma atual).
                    "status": r["status"],
                    "velocidade": r["speed"],
                    "motorista": r["driver_name"],
                    "area": r["area_name"],
                    "visualizado": bool(r["user_view"]),
                    "observacao": r["observacao"],
                },
                acknowledged=r["acknowledged"],
                acknowledged_by=r["acknowledged_by"],
                acknowledged_at=r["acknowledged_at"],
            )
            for r in rows
        ],
        total=rows[0]["total_count"] if rows else 0,
        summary=EventSummary(**resumo),
        limit=limit,
        offset=offset,
    )


@router.get("/alarmes")
async def listar_alarmes(
    horas: int = Query(24, ge=1, le=720, description="Janela, em horas. Padrão: 24."),
    group_id: Optional[int] = Query(None),
    so_nao_visualizados: bool = Query(False),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Disparos do Monitor de Alarmes (`alarm_violation`), mais recentes primeiro.

    Mesmos filtros do `listViolationAction` do plataforma_web (disparo aberto,
    alarme ativo e exibido no monitor, unidade ativa, grupo/subgrupo do acesso,
    mesma conta), com uma diferença deliberada: **ordena por data**. Lá o
    `ORDER BY` está comentado e o monitor pega 200 disparos quaisquer (vault:
    Alarmes, seção 5).
    """
    filtro_grupo = " AND al.group_id = :group_id" if group_id is not None else ""
    filtro_visto = " AND COALESCE(av.user_view, 0) = 0" if so_nao_visualizados else ""
    params = {"user_id": current_user.user_id, "horas": horas, "limit": limit}
    if group_id is not None:
        params["group_id"] = group_id

    linhas = (
        await db.execute(
            text(
                f"""
                SELECT av.id, al.name AS alarme, al.level AS nivel, av.initial_time, av.final_time,
                       tu.id AS unit_id, tu.label AS placa, tu.label2 AS prefixo,
                       NULLIF(TRIM(av.driver_name), '') AS motorista, av.speed AS velocidade,
                       NULLIF(TRIM(av.address), '') AS endereco, av.lat, av.lon,
                       COALESCE(av.user_view, 0) AS visualizado, av.obs_modified AS observacao,
                       COUNT(*) OVER () AS total
                FROM mova.alarm_violation av
                JOIN mova.alarm al ON av.alarm_id = al.id AND al.status = 1
                JOIN mova.tracked_unit tu ON av.unit_id = tu.id AND tu.status = 1
                WHERE av.status = 1
                  AND al.notif_monitor
                  AND av.initial_time >= (now() AT TIME ZONE 'America/Sao_Paulo') - make_interval(hours => :horas)
                  AND al.group_id IN (SELECT group_id FROM mova.user_group_access WHERE user_id = :user_id)
                  AND (
                      al.subgroup_id IN (SELECT subgroup_id FROM mova.user_group_access WHERE user_id = :user_id)
                      OR al.subgroup_id IS NULL OR al.subgroup_id = 0
                  )
                  AND al.account_id = (SELECT account_id FROM mova.users WHERE id = :user_id)
                  {filtro_grupo}
                  {filtro_visto}
                ORDER BY av.initial_time DESC
                LIMIT :limit
                """
            ),
            params,
        )
    ).mappings().all()

    itens = [
        {
            "id": r["id"],
            "alarme": r["alarme"],
            # 3 é o nível que toca o som no monitor antigo.
            "nivel": r["nivel"],
            "inicio": r["initial_time"].isoformat() if r["initial_time"] else None,
            "fim": r["final_time"].isoformat() if r["final_time"] else None,
            "unit_id": r["unit_id"],
            "placa": r["placa"],
            "prefixo": r["prefixo"],
            "motorista": r["motorista"],
            "velocidade": r["velocidade"],
            "endereco": r["endereco"],
            "latitude": float(r["lat"]) if r["lat"] not in (None, "") else None,
            "longitude": float(r["lon"]) if r["lon"] not in (None, "") else None,
            "visualizado": bool(r["visualizado"]),
            "observacao": r["observacao"],
        }
        for r in linhas
    ]
    return {
        "janela_horas": horas,
        "total": linhas[0]["total"] if linhas else 0,
        "nao_visualizados": sum(1 for i in itens if not i["visualizado"]),
        "itens": itens,
    }


@router.get("/alarmes/nao-visualizados", response_model=AlarmesNaoVisualizados)
async def contar_alarmes_nao_visualizados(
    horas: int = Query(24, ge=1, le=720, description="Janela, em horas. Padrão: 24."),
    db: AsyncSession = Depends(get_db_read),
    current_user=Depends(require_permission("reports", "read")),
):
    """
    Disparos não visualizados, pela regra do Monitor de Alarmes do plataforma_web.

    Os filtros reproduzem
    `alarmController::listViolationAction`: disparo aberto (`status = 1`),
    alarme ativo e exibido no monitor, unidade ativa, grupo e subgrupo do
    acesso do usuário, mesma conta. "Não visualizado" é `user_view` 0 ou nulo.

    Diferença deliberada: só conta a janela pedida (24 h por decisão de
    produto). O total em aberto passa de dez mil e não serve para agir.

    `initial_time` é gravado em horário de Brasília, sem fuso. O corte é
    calculado no mesmo relógio, para não depender do fuso da conexão.
    """
    total = (
        await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM mova.alarm_violation av
                JOIN mova.alarm al ON av.alarm_id = al.id AND al.status = 1
                JOIN mova.tracked_unit tu ON av.unit_id = tu.id AND tu.status = 1
                WHERE av.status = 1
                  AND al.notif_monitor
                  AND COALESCE(av.user_view, 0) = 0
                  AND av.initial_time >= (now() AT TIME ZONE 'America/Sao_Paulo')
                                         - make_interval(hours => :horas)
                  AND al.group_id IN (
                      SELECT group_id FROM mova.user_group_access WHERE user_id = :user_id
                  )
                  AND (
                      al.subgroup_id IN (
                          SELECT subgroup_id FROM mova.user_group_access WHERE user_id = :user_id
                      )
                      OR al.subgroup_id IS NULL
                      OR al.subgroup_id = 0
                  )
                  AND al.account_id = (SELECT account_id FROM mova.users WHERE id = :user_id)
                """
            ),
            {"user_id": current_user.user_id, "horas": horas},
        )
    ).scalar_one()

    return AlarmesNaoVisualizados(nao_visualizados=total, janela_horas=horas)


@router.post(
    "/{event_id}/acknowledge",
    response_model=EventResponse,
    responses={501: {"description": "Tratativa pela API desativada"}},
)
async def acknowledge_event(
    event_id: int,
    payload: EventAcknowledge,
    user: User = Depends(require_permission("reports", "read")),
):
    """
    Desativado: responde 501 sem tocar no banco.

    Gravava em `fleet_events`, mas os eventos listados agora vêm de
    `alarm_violation` — o mesmo id apontaria para outro registro — e o banco
    de produção é somente leitura para esta API. A tratativa continua sendo
    feita no Monitor de Alarmes da plataforma atual.
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "Tratativa pela API desativada: os alarmes vêm de alarm_violation e são tratados no Monitor de Alarmes.",
    )
