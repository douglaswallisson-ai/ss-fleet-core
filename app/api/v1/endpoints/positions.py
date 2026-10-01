"""
Posições atuais da frota.

Lê ``mova.dev_status``, que guarda a última leitura de cada equipamento. É a
fonte do mapa ao vivo.

Não confundir com ``/reports/history``: aquele devolve o rastro histórico, uma
linha por posição registrada. Aqui é uma linha por veículo, sempre a mais
recente — é o que a tela precisa para desenhar a frota agora.
"""

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_read
from app.core.escopo import clausula_escopo, escopo_do_usuario
from app.middleware.auth import require_permission
from app.models.user import User
from app.schemas.positions import PositionResponse, PositionsResponse

router = APIRouter()

#: Acima disto o veículo é considerado sem comunicação.
#:
#: Vinte minutos cobre túnel, garagem coberta e zona sem cobertura sem marcar
#: como perdido quem está apenas passando por elas. Abaixo disso a tela ficaria
#: acusando falha o tempo todo em operação urbana.
MINUTOS_SEM_SINAL = 20


@router.get("/", response_model=PositionsResponse)
async def list_positions(
    only_moving: bool = Query(False, description="Apenas veículos em movimento"),
    max_age_minutes: int = Query(
        1440,
        ge=1,
        le=10080,
        description="Idade máxima da leitura, em minutos. Padrão: 24 h.",
    ),
    group_id: Optional[int] = Query(
        None,
        description="Restringe a uma empresa, dentro do escopo do usuário (mesmo filtro de /vehicles).",
    ),
    db: AsyncSession = Depends(get_db_read),
    current_user: User = Depends(require_permission("vehicles", "read")),
):
    """
    Última posição conhecida de cada veículo do escopo do usuário.

    A janela de idade existe porque `dev_status` guarda a última leitura
    **para sempre**: veículo que parou de transmitir há oito meses continua
    lá, com a coordenada de onde estava. Sem o corte, o mapa mostraria a frota
    inteira como se estivesse em campo.
    """
    grupos, subgrupos = escopo_do_usuario(current_user)
    escopo_sql, escopo_params = clausula_escopo(grupos, subgrupos, alias="tu")

    limite = datetime.now() - timedelta(minutes=max_age_minutes)
    sem_sinal = datetime.now() - timedelta(minutes=MINUTOS_SEM_SINAL)

    filtro_movimento = " AND COALESCE(ds.speed, 0) > 3" if only_moving else ""

    # Recorte por empresa, **além** do escopo — nunca no lugar dele: um
    # group_id fora do acesso do usuário continua sem devolver nada. Sem este
    # filtro o mapa mostrava todas as empresas do usuário enquanto a lista de
    # veículos mostrava só a escolhida no seletor.
    filtro_grupo = " AND tu.group_id = :group_id" if group_id is not None else ""
    params_grupo = {"group_id": group_id} if group_id is not None else {}

    linhas = (
        await db.execute(
            text(
                f"""
                SELECT
                    ds.unit_id,
                    tu.label        AS placa,
                    tu.label2       AS prefixo,
                    ds.latitude,
                    ds.longitude,
                    ds.speed,
                    ds.ignition,
                    ds.address,
                    ds.local_time,
                    ds.odom,
                    tu.group_id,
                    tu.subgroup_id
                FROM mova.dev_status ds
                JOIN mova.tracked_unit tu ON tu.id = ds.unit_id AND tu.status = 1
                WHERE ds.latitude IS NOT NULL
                  AND ds.longitude IS NOT NULL
                  -- Coordenada zero é leitura sem fixo de GPS, não a costa da
                  -- África. Sem este filtro a frota aparece no golfo da Guiné.
                  AND ds.latitude <> 0
                  AND ds.longitude <> 0
                  AND ds.local_time >= :limite
                  {filtro_movimento}
                  {filtro_grupo}
                  {escopo_sql}
                ORDER BY ds.local_time DESC
                """
            ),
            {"limite": limite, **escopo_params, **params_grupo},
        )
    ).mappings().all()

    itens = [
        PositionResponse(
            unit_id=r["unit_id"],
            placa=r["placa"],
            prefixo=r["prefixo"],
            latitude=float(r["latitude"]),
            longitude=float(r["longitude"]),
            speed=float(r["speed"]) if r["speed"] is not None else None,
            ignition=r["ignition"],
            address=r["address"],
            local_time=r["local_time"],
            # Em metros, como o resto da telemetria.
            odom=r["odom"],
            group_id=r["group_id"],
            subgroup_id=r["subgroup_id"],
            sem_sinal=bool(r["local_time"] and r["local_time"] < sem_sinal),
        )
        for r in linhas
    ]

    return PositionsResponse(
        items=itens,
        total=len(itens),
        # A tela precisa do critério para explicar o número ao usuário, em vez
        # de apresentá-lo como verdade sem contexto.
        minutos_sem_sinal=MINUTOS_SEM_SINAL,
        janela_minutos=max_age_minutes,
    )
