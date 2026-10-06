"""Motor de rotas da roteirização, com desvio das áreas de risco.

Decisão do PM/CEO (06/10/2026): usar um motor que aceite "evite esta área".
Google Routes não aceita área desenhada (só pedágio, rodovia e balsa) e o
Waze não oferece cálculo de rota para terceiros. Ficam prontos aqui, e o TI
escolhe qual ligar pelo `.env` (ROTEADOR):

- "aws"      Amazon Location Service, Routes v2 (recomendado: a empresa já
             está na AWS). Precisa de AWS_LOCATION_API_KEY e da região.
             Caminhão e áreas a evitar em polígono.
- "here"     HERE Routing v8 (HERE_API_KEY). Caminhão e áreas a evitar;
             aqui as áreas vão como retângulo envolvente (mais conservador).
- "valhalla" Servidor próprio com mapa do OpenStreetMap (VALHALLA_URL).
             Sem custo por consulta; caminhão e polígonos a evitar.
- "osrm"     Servidor OSRM antigo (ROTEIRIZADOR_OSRM_URL). Segue a malha
             viária mas NÃO desvia de áreas.

Sem nenhum configurado, a roteirização segue estimando em linha reta e só
AVISA quando o caminho passa por uma área de risco.

Retorno comum: {"motor", "trechos": [{"km", "min"}], "geometria": [[lat, lng]],
"desvia_areas": bool}.
"""

from typing import Optional

import httpx

from app.core import geo
from app.core.config import settings

#: Áreas mandadas ao motor: as mais próximas do caminho, até este limite.
MAX_AREAS = 30
#: Vértices por área (simplifica as maiores).
MAX_VERTICES = 60


def motor_configurado() -> str:
    m = (getattr(settings, "ROTEADOR", "") or "").strip().lower()
    if m == "aws" and getattr(settings, "AWS_LOCATION_API_KEY", ""):
        return "aws"
    if m == "here" and getattr(settings, "HERE_API_KEY", ""):
        return "here"
    if m == "valhalla" and getattr(settings, "VALHALLA_URL", ""):
        return "valhalla"
    if getattr(settings, "ROTEIRIZADOR_OSRM_URL", ""):
        return "osrm"
    return ""


NOME_MOTOR = {"aws": "Amazon Location", "here": "HERE", "valhalla": "servidor próprio (Valhalla)", "osrm": "OSRM"}


def _anel_curto(anel: list) -> list:
    a = geo.simplificar(anel, MAX_VERTICES)
    if a[0] != a[-1]:
        a = a + [a[0]]
    return a


def areas_perto(areas: list[dict], pontos: list[list[float]], margem_graus: float = 0.5) -> list[list]:
    """Anéis das áreas a evitar que ficam na região da viagem."""
    b0, p0, b1, p1 = geo.caixa(pontos)
    b0, p0, b1, p1 = b0 - margem_graus, p0 - margem_graus, b1 + margem_graus, p1 + margem_graus
    cand = []
    for a in areas:
        if a["nivel"] != "evitar":
            continue
        for anel in a["aneis"]:
            a0, o0, a1, o1 = geo.caixa(anel, latlng=False)
            if a1 < b0 or a0 > b1 or o1 < p0 or o0 > p1:
                continue
            centro = ((a0 + a1) / 2, (o0 + o1) / 2)
            cand.append((geo.dist_ponto_linha_m(centro[0], centro[1], pontos), _anel_curto(anel)))
    cand.sort(key=lambda x: x[0])
    return [x[1] for x in cand[:MAX_AREAS]]


async def _aws(pontos, aneis, perfil) -> Optional[dict]:
    regiao = getattr(settings, "AWS_LOCATION_REGION", "") or "sa-east-1"
    url = f"https://routes.geo.{regiao}.amazonaws.com/v2/routes"
    corpo = {
        "Origin": [pontos[0][1], pontos[0][0]],
        "Destination": [pontos[-1][1], pontos[-1][0]],
        "TravelMode": "Car" if perfil == "carro" else "Truck",
        "LegGeometryFormat": "Simple",
        "LegAdditionalFeatures": ["Summary"],
    }
    if len(pontos) > 2:
        corpo["Waypoints"] = [{"Position": [p[1], p[0]]} for p in pontos[1:-1]]
    if aneis:
        corpo["Avoid"] = {"Areas": [{"Geometry": {"Polygon": [anel]}} for anel in aneis]}
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(url, params={"key": settings.AWS_LOCATION_API_KEY}, json=corpo)
        r.raise_for_status()
        rota = r.json()["Routes"][0]
    trechos, linha = [], []
    for leg in rota.get("Legs", []):
        pts = [[c[1], c[0]] for c in (leg.get("Geometry") or {}).get("LineString", [])]
        linha += pts if not linha else pts[1:]
        resumo = ((leg.get("VehicleLegDetails") or {}).get("Summary") or {}).get("Overview") or {}
        km = (resumo.get("Distance") or 0) / 1000 or (geo.km_ate_vertice(pts)[-1] if len(pts) > 1 else 0)
        mn = (resumo.get("Duration") or 0) / 60
        trechos.append({"km": km, "min": mn})
    total = (rota.get("Summary") or {})
    if trechos and not any(t["min"] for t in trechos) and total.get("Duration"):
        soma_km = sum(t["km"] for t in trechos) or 1
        for t in trechos:
            t["min"] = total["Duration"] / 60 * t["km"] / soma_km
    return {"trechos": trechos, "geometria": linha}


async def _here(pontos, aneis, perfil) -> Optional[dict]:
    params = {
        "transportMode": "car" if perfil == "carro" else "truck",
        "origin": f"{pontos[0][0]},{pontos[0][1]}",
        "destination": f"{pontos[-1][0]},{pontos[-1][1]}",
        "return": "polyline,summary",
        "apikey": settings.HERE_API_KEY,
    }
    via = [f"{p[0]},{p[1]}" for p in pontos[1:-1]]
    if aneis:
        caixas = []
        for anel in aneis:
            a0, o0, a1, o1 = geo.caixa(anel, latlng=False)
            caixas.append(f"bbox:{o0:.5f},{a0:.5f},{o1:.5f},{a1:.5f}")
        params["avoid[areas]"] = "|".join(caixas)
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.get("https://router.hereapi.com/v8/routes", params=[*params.items(), *[("via", v) for v in via]])
        r.raise_for_status()
        rota = r.json()["routes"][0]
    trechos, linha = [], []
    for s in rota.get("sections", []):
        pts = geo.decodificar_flexivel(s.get("polyline", ""))
        linha += pts if not linha else pts[1:]
        resumo = s.get("summary") or {}
        trechos.append({"km": (resumo.get("length") or 0) / 1000, "min": (resumo.get("duration") or 0) / 60})
    return {"trechos": trechos, "geometria": linha}


async def _valhalla(pontos, aneis, perfil) -> Optional[dict]:
    corpo = {
        "locations": [{"lat": p[0], "lon": p[1], "type": "break"} for p in pontos],
        "costing": {"carro": "auto", "onibus": "bus"}.get(perfil, "truck"),
        "directions_options": {"units": "kilometers"},
    }
    if aneis:
        corpo["exclude_polygons"] = aneis
    async with httpx.AsyncClient(timeout=20) as cli:
        r = await cli.post(f"{settings.VALHALLA_URL.rstrip('/')}/route", json=corpo)
        r.raise_for_status()
        trip = r.json()["trip"]
    trechos, linha = [], []
    for leg in trip.get("legs", []):
        pts = geo.decodificar_polyline(leg.get("shape", ""), 6)
        linha += pts if not linha else pts[1:]
        resumo = leg.get("summary") or {}
        trechos.append({"km": float(resumo.get("length") or 0), "min": float(resumo.get("time") or 0) / 60})
    return {"trechos": trechos, "geometria": linha}


async def _osrm(pontos, aneis, perfil) -> Optional[dict]:
    coords = ";".join(f"{p[1]},{p[0]}" for p in pontos)
    async with httpx.AsyncClient(timeout=15) as cli:
        r = await cli.get(f"{settings.ROTEIRIZADOR_OSRM_URL.rstrip('/')}/route/v1/driving/{coords}",
                          params={"overview": "simplified", "geometries": "geojson"})
        r.raise_for_status()
        rota = r.json()["routes"][0]
    return {"trechos": [{"km": leg["distance"] / 1000, "min": leg["duration"] / 60} for leg in rota["legs"]],
            "geometria": [[c[1], c[0]] for c in rota["geometry"]["coordinates"]]}


_MOTORES = {"aws": _aws, "here": _here, "valhalla": _valhalla, "osrm": _osrm}


async def calcular(pontos: list[list[float]], areas: list[dict], perfil: str = "caminhao") -> Optional[dict]:
    """Rota pela malha viária desviando das áreas "evitar". None = sem motor ou falha (a tela estima)."""
    m = motor_configurado()
    if not m:
        return None
    aneis = areas_perto(areas, pontos) if m != "osrm" else []
    try:
        r = await _MOTORES[m](pontos, aneis, perfil)
    except Exception as e:  # motor fora do ar ou chave inválida: a roteirização cai na estimativa
        return {"erro": f"{NOME_MOTOR[m]}: {str(e)[:200]}", "motor": m}
    if not r or not r.get("trechos") or len(r["trechos"]) != len(pontos) - 1:
        return {"erro": f"{NOME_MOTOR[m]} não devolveu a rota.", "motor": m}
    r.update({"motor": m, "desvia_areas": m != "osrm", "areas_enviadas": len(aneis)})
    return r
