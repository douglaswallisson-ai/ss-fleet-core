"""
API v1 router aggregation.
Combines all endpoint routers.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import fleet_health, positions, auth, vehicles, devices, tracked_unit_devices, vcms_unit_devices, drivers, driver_ranking, gerencial, bi, ai_fleet, relevo, operacao, escala_viagem, mapa, suporte, embed, acessos, sinotico, manutencao, timeline, jornada, combustivel, cliente, roteirizacao, cadastros, passageiros, relatorios_frota, reports, groups, subgroups, history_detailed, bus_lines, tracking, events, video, cameras, contratos, indicators, pois
from app.routers.admin import permissions_router, user_group_access_router

api_router = APIRouter()

# Include endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(groups.router, prefix="/groups", tags=["Groups"])
api_router.include_router(subgroups.router, prefix="/subgroups", tags=["Subgroups"])
api_router.include_router(vehicles.router, prefix="/vehicles", tags=["Vehicles"])
api_router.include_router(positions.router, prefix="/positions", tags=["positions"])
api_router.include_router(fleet_health.router, prefix="/fleet-health", tags=["fleet-health"])
api_router.include_router(devices.router, prefix="/devices", tags=["Devices"])
api_router.include_router(drivers.router, prefix="/drivers", tags=["Drivers"])
# Prefixo próprio, e não /drivers/ranking: /drivers/{driver_id} captura
# qualquer segmento e responderia 422 antes de chegar aqui.
api_router.include_router(driver_ranking.router, prefix="/driver-ranking", tags=["Drivers"])
api_router.include_router(gerencial.router, prefix="/gerencial", tags=["Gerencial"])
api_router.include_router(bi.router, prefix="/bi", tags=["Gerencial"])
api_router.include_router(ai_fleet.router, prefix="/ai-fleet", tags=["IA Ops Advisor"])
api_router.include_router(relevo.router, prefix="/relevo", tags=["Relevo"])
api_router.include_router(operacao.router, prefix="/operacao", tags=["Operação de linhas"])
api_router.include_router(escala_viagem.router, prefix="/escala-viagem", tags=["Escala de Viagem"])
api_router.include_router(mapa.router, prefix="/mapa", tags=["Mapa"])
api_router.include_router(suporte.router, prefix="/suporte", tags=["Suporte"])
api_router.include_router(embed.router, prefix="/embed", tags=["Embutido"])
api_router.include_router(acessos.router, prefix="/acessos", tags=["Acessos"])
api_router.include_router(sinotico.router, prefix="/sinotico", tags=["Painel sinótico"])
api_router.include_router(manutencao.router, prefix="/manutencao", tags=["Manutenção"])
api_router.include_router(timeline.router, prefix="/eventos/timeline", tags=["Eventos"])
api_router.include_router(jornada.router, prefix="/jornada", tags=["Jornada"])
api_router.include_router(combustivel.router, prefix="/combustivel", tags=["Combustível"])
api_router.include_router(cliente.router, prefix="/cliente", tags=["Cliente"])
api_router.include_router(roteirizacao.router, prefix="/roteirizacao", tags=["Roteirização"])
api_router.include_router(cadastros.router, prefix="/cadastros", tags=["Cadastros"])
api_router.include_router(passageiros.router, prefix="/passageiros", tags=["Passageiros"])
api_router.include_router(relatorios_frota.router, prefix="/relatorios-frota", tags=["Relatórios de frota"])
api_router.include_router(bus_lines.router, prefix="/bus-lines", tags=["Bus Lines"])
api_router.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
api_router.include_router(events.router, prefix="/events", tags=["Events"])
api_router.include_router(video.router, prefix="/video", tags=["Video"])
api_router.include_router(cameras.router, prefix="/cameras", tags=["Câmeras ao vivo"])
api_router.include_router(contratos.router, prefix="/contratos", tags=["Contratos"])
api_router.include_router(indicators.router, prefix="/indicators", tags=["Indicators"])
api_router.include_router(pois.router, prefix="/pois", tags=["POIs"])
api_router.include_router(tracked_unit_devices.router, prefix="/device-associations", tags=["Device Associations"])
api_router.include_router(vcms_unit_devices.router, prefix="/video-device-associations", tags=["Video Device Associations"])
api_router.include_router(reports.router, prefix="/reports")
api_router.include_router(history_detailed.router, prefix="/reports")

# Admin routers
api_router.include_router(permissions_router)
api_router.include_router(user_group_access_router)
