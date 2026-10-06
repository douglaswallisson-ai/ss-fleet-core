"""Geometria simples sem dependências: distância, ponto dentro de área e
decodificação das linhas devolvidas pelos motores de rota.

Convenção: linhas de rota em [[lat, lng], ...] (como o front desenha);
anéis de área em [[lng, lat], ...] (como GeoJSON e os motores esperam).
A projeção é plana local: erro desprezível nas distâncias de centenas de
metros usadas nos avisos.
"""

import math
from typing import Iterable, Optional

M_POR_GRAU = 111_320.0


def haversine_m(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> float:
    p = math.pi / 180
    h = math.sin((b_lat - a_lat) * p / 2) ** 2 + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lng - a_lng) * p / 2) ** 2
    return 12_742_000 * math.asin(math.sqrt(h))


def dist_ponto_segmento_m(lat: float, lng: float, a: list, b: list) -> float:
    """Distância de um ponto a um segmento a=[lat, lng], b=[lat, lng]."""
    kx = M_POR_GRAU * math.cos(math.radians(lat))
    ax, ay = (a[1] - lng) * kx, (a[0] - lat) * M_POR_GRAU
    bx, by = (b[1] - lng) * kx, (b[0] - lat) * M_POR_GRAU
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / (dx * dx + dy * dy)))
    return math.hypot(ax + t * dx, ay + t * dy)


def dist_ponto_linha_m(lat: float, lng: float, linha: list) -> float:
    if not linha:
        return float("inf")
    if len(linha) == 1:
        return haversine_m(lat, lng, linha[0][0], linha[0][1])
    return min(dist_ponto_segmento_m(lat, lng, a, b) for a, b in zip(linha, linha[1:]))


def km_ate_vertice(linha: list) -> list[float]:
    """Quilômetro acumulado em cada vértice da linha."""
    out, s = [0.0], 0.0
    for a, b in zip(linha, linha[1:]):
        s += haversine_m(a[0], a[1], b[0], b[1]) / 1000
        out.append(s)
    return out


def km_na_linha(lat: float, lng: float, linha: list, acumulado: Optional[list[float]] = None) -> float:
    """Em que quilômetro da rota fica o ponto (pelo vértice mais próximo)."""
    acumulado = acumulado or km_ate_vertice(linha)
    kx = M_POR_GRAU * math.cos(math.radians(lat))
    i = min(range(len(linha)), key=lambda k: ((linha[k][0] - lat) * M_POR_GRAU) ** 2 + ((linha[k][1] - lng) * kx) ** 2)
    return acumulado[i]


def dentro(lat: float, lng: float, anel: list) -> bool:
    """Ponto dentro do polígono (anel [[lng, lat], ...]), por cruzamento de raio."""
    ok = False
    j = len(anel) - 1
    for i in range(len(anel)):
        xi, yi = anel[i][0], anel[i][1]
        xj, yj = anel[j][0], anel[j][1]
        if (yi > lat) != (yj > lat) and lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            ok = not ok
        j = i
    return ok


def caixa(pontos: Iterable, latlng: bool = True) -> tuple[float, float, float, float]:
    """(min_lat, min_lng, max_lat, max_lng)."""
    lats, lngs = [], []
    for p in pontos:
        la, ln = (p[0], p[1]) if latlng else (p[1], p[0])
        lats.append(la)
        lngs.append(ln)
    return min(lats), min(lngs), max(lats), max(lngs)


def _cruza(p1, p2, p3, p4) -> bool:
    def lado(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = lado(p3, p4, p1), lado(p3, p4, p2), lado(p1, p2, p3), lado(p1, p2, p4)
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def linha_entra_na_area(linha: list, anel: list) -> bool:
    """A rota ([[lat, lng]]) entra na área (anel [[lng, lat]])?"""
    if not linha or len(anel) < 3:
        return False
    a0, o0, a1, o1 = caixa(anel, latlng=False)
    b0, p0, b1, p1 = caixa(linha)
    if b1 < a0 or b0 > a1 or p1 < o0 or p0 > o1:
        return False
    if any(dentro(la, ln, anel) for la, ln in linha):
        return True
    arestas = [((anel[i][1], anel[i][0]), (anel[i + 1][1], anel[i + 1][0])) for i in range(len(anel) - 1)]
    for a, b in zip(linha, linha[1:]):
        for c, d in arestas:
            if _cruza(a, b, c, d):
                return True
    return False


def dist_area_linha_m(anel: list, linha: list) -> float:
    """Menor distância entre a área e a rota (0 se a rota entra nela)."""
    if linha_entra_na_area(linha, anel):
        return 0.0
    # Vértices da área contra a rota e vértices da rota contra as arestas da área.
    d1 = min(dist_ponto_linha_m(p[1], p[0], linha) for p in anel)
    borda = [[p[1], p[0]] for p in anel]
    d2 = min(dist_ponto_linha_m(la, ln, borda) for la, ln in linha[:: max(1, len(linha) // 400)])
    return min(d1, d2)


def simplificar(linha: list, max_pontos: int = 500) -> list:
    """Reduz a linha para no máximo `max_pontos` vértices (mantém início e fim)."""
    if len(linha) <= max_pontos:
        return linha
    passo = (len(linha) - 1) / (max_pontos - 1)
    return [linha[round(i * passo)] for i in range(max_pontos)]


# ------------------------------------------------------------ decodificadores

def decodificar_polyline(s: str, precisao: int = 6) -> list[list[float]]:
    """Polyline do Google com 6 casas (usada pelo Valhalla) → [[lat, lng]]."""
    out, i, lat, lng, f = [], 0, 0, 0, 10 ** precisao
    while i < len(s):
        for eixo in (0, 1):
            res, desloc = 0, 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                res |= (b & 0x1F) << desloc
                desloc += 5
                if b < 0x20:
                    break
            d = ~(res >> 1) if res & 1 else res >> 1
            if eixo == 0:
                lat += d
            else:
                lng += d
        out.append([lat / f, lng / f])
    return out


_TAB = {c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")}


def decodificar_flexivel(s: str) -> list[list[float]]:
    """Flexible polyline da HERE → [[lat, lng]]."""
    vals, res, desloc = [], 0, 0
    for c in s:
        v = _TAB[c]
        res |= (v & 0x1F) << desloc
        if v & 0x20:
            desloc += 5
        else:
            vals.append(res)
            res, desloc = 0, 0
    if len(vals) < 2:
        return []
    cab = vals[1]
    prec, terc = cab & 15, (cab >> 4) & 7
    f = 10 ** prec
    passo = 3 if terc else 2
    out, lat, lng = [], 0, 0
    for k in range(2, len(vals) - passo + 1, passo):
        dl = vals[k]
        dn = vals[k + 1]
        lat += ~(dl >> 1) if dl & 1 else dl >> 1
        lng += ~(dn >> 1) if dn & 1 else dn >> 1
        out.append([lat / f, lng / f])
    return out
