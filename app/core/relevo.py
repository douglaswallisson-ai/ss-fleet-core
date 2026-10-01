"""
Relevo pelo mapa de elevação (SRTM, NASA), lido de `data/relevo/`.

Só 39% da frota tem equipamento que grava altitude (VIRLOC 8 e GV75MG); os
demais mandam zero. Latitude e longitude todos mandam — então a elevação de
cada posição sai do mapa, igual para qualquer equipamento. O arquivo de cada
quadrado de 1° tem 1201 × 1201 pontos (~90 m), gravado por
`scripts/baixar_relevo.py`.

Sem numpy de propósito: o backend não tem a biblioteca e a conta por ponto
é pequena (4 leituras e uma interpolação).
"""

import math
import mmap
from functools import lru_cache
from pathlib import Path
from struct import unpack_from
from typing import Iterable, Optional

PASTA = Path(__file__).resolve().parents[2] / "data" / "relevo"
LADO = 1201
VAZIO = -32768

#: Inclinação a partir da qual o trecho conta como aclive (2%).
ACLIVE = 0.02
#: Pontos mais distantes que isto não formam trecho: houve buraco no sinal.
MAX_TRECHO_KM = 5.0


def nome_quadrado(lat: int, lon: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


@lru_cache(maxsize=512)
def _quadrado(lat: int, lon: int) -> Optional[mmap.mmap]:
    arq = PASTA / f"{nome_quadrado(lat, lon)}.h3"
    if not arq.exists():
        return None
    with open(arq, "rb") as fh:
        return mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)


def mapa_disponivel() -> bool:
    return PASTA.exists() and any(PASTA.glob("*.h3"))


def elevacao(lat: float, lon: float) -> Optional[float]:
    """Elevação em metros (interpolação bilinear), ou None sem mapa no local."""
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    la, lo = math.floor(lat), math.floor(lon)
    m = _quadrado(la, lo)
    if m is None:
        return None
    # Linha 0 é a borda norte do quadrado.
    y = (la + 1 - lat) * (LADO - 1)
    x = (lon - lo) * (LADO - 1)
    r0, c0 = min(int(y), LADO - 2), min(int(x), LADO - 2)
    fy, fx = y - r0, x - c0
    vals = []
    for dr, dc, w in ((0, 0, (1 - fy) * (1 - fx)), (0, 1, (1 - fy) * fx), (1, 0, fy * (1 - fx)), (1, 1, fy * fx)):
        v = unpack_from("<h", m, ((r0 + dr) * LADO + c0 + dc) * 2)[0]
        if v != VAZIO:
            vals.append((v, w))
    if not vals:
        return None
    peso = sum(w for _, w in vals)
    return sum(v * w for v, w in vals) / peso if peso else float(vals[0][0])


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = 0.5 - math.cos((lat2 - lat1) * p) / 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lon2 - lon1) * p)) / 2
    return 12742 * math.asin(math.sqrt(max(0.0, a)))


def acumular(pontos: Iterable[tuple[float, float]]):
    """
    Percorre (lat, lon) em ordem e devolve, para cada ponto,
    (distância acumulada km, elevação m) — e o resumo do percurso.

    Subida e descida só somam entre pontos próximos (≤ 5 km): um buraco no
    sinal ligaria dois pontos distantes por uma reta que não existiu.
    """
    serie: list[tuple[float, Optional[float]]] = []
    km = sub = desc = km_aclive = km_declive = km_medido = 0.0
    ant = None
    for lat, lon in pontos:
        e = elevacao(lat, lon)
        if ant is not None:
            d = distancia_km(ant[0], ant[1], lat, lon)
            km += d
            if e is not None and ant[2] is not None and 0 < d <= MAX_TRECHO_KM:
                dz = e - ant[2]
                km_medido += d
                if dz > 0:
                    sub += dz
                else:
                    desc -= dz
                incl = dz / (d * 1000)
                if incl >= ACLIVE:
                    km_aclive += d
                elif incl <= -ACLIVE:
                    km_declive += d
        serie.append((km, e))
        ant = (lat, lon, e)
    return serie, {
        "km": km,
        "km_medido": km_medido,
        "subida_m": sub,
        "descida_m": desc,
        "km_aclive": km_aclive,
        "km_declive": km_declive,
    }
