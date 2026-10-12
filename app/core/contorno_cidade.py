"""
Contorno oficial do município (malha do IBGE), para o CCO mostrar só a cidade
— "a nível de Florianópolis, sem ver mais nada ao redor", como o mapa da planta
de uma mineração (PM, 12/10/2026).

Fonte pública: servicodados.ibge.gov.br (localidades + malhas, qualidade máxima,
~10 KB por cidade). Fica guardado em `data/cidades/` depois da primeira vez.
"""

import json
import unicodedata
from pathlib import Path
from typing import Optional

import httpx

PASTA = Path(__file__).resolve().parents[2] / "data" / "cidades"
IBGE = "https://servicodados.ibge.gov.br/api"
_MEMORIA: dict[str, dict] = {}


def _chave(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


async def _json(url: str):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url)
        r.raise_for_status()
        return r.json()


async def _sigla(uf: str) -> Optional[str]:
    if len(uf.strip()) == 2:
        return uf.strip().upper()
    for e in await _json(f"{IBGE}/v1/localidades/estados"):
        if _chave(e["nome"]) == _chave(uf):
            return e["sigla"]
    return None


def montar(feature_collection: dict, ibge_id: int, nome: str, uf: str) -> dict:
    """GeoJSON do IBGE ([lng, lat]) → polígonos em [lat, lng] (como o front desenha) e a caixa."""
    poligonos: list[list[list[float]]] = []
    for f in feature_collection.get("features", []):
        g = f.get("geometry") or {}
        partes = g.get("coordinates", [])
        if g.get("type") == "Polygon":
            partes = [partes]
        for pol in partes:
            if pol:
                poligonos.append([[round(p[1], 5), round(p[0], 5)] for p in pol[0]])  # anel externo
    lats = [p[0] for pol in poligonos for p in pol]
    lngs = [p[1] for pol in poligonos for p in pol]
    caixa = [min(lats), min(lngs), max(lats), max(lngs)] if lats else None
    return {"ibge_id": ibge_id, "nome": nome, "uf": uf, "poligonos": poligonos, "caixa": caixa}


async def contorno(cidade: str, uf: str) -> Optional[dict]:
    """Contorno do município pelo nome e estado (nome ou sigla). None se não achar."""
    k = f"{_chave(cidade)}|{_chave(uf)}"
    if k in _MEMORIA:
        return _MEMORIA[k]
    PASTA.mkdir(parents=True, exist_ok=True)
    arq = PASTA / (k.replace("|", "_").replace(" ", "-") + ".json")
    if arq.exists():
        _MEMORIA[k] = json.loads(arq.read_text(encoding="utf-8"))
        return _MEMORIA[k]
    sigla = await _sigla(uf)
    if not sigla:
        return None
    mun = next((m for m in await _json(f"{IBGE}/v1/localidades/estados/{sigla}/municipios")
                if _chave(m["nome"]) == _chave(cidade)), None)
    if not mun:
        return None
    fc = await _json(f"{IBGE}/v3/malhas/municipios/{mun['id']}?formato=application/vnd.geo%2Bjson&qualidade=maxima")
    d = montar(fc, mun["id"], mun["nome"], sigla)
    arq.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    _MEMORIA[k] = d
    return d
