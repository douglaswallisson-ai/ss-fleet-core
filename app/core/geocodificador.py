"""Endereço → coordenada, para a rota automática de fretamento.

Motor: HERE Geocoding quando há HERE_API_KEY no .env (melhor com número de casa no Brasil);
senão o Nominatim do OpenStreetMap, que pede no máximo 1 consulta por segundo e identificação
do sistema — serve para piloto, não para uso pesado (política de uso do OSM).

Só o endereço sai daqui (nunca o nome do funcionário). As respostas ficam guardadas em
`data/rotas.sqlite` (tabela geocodificacao): o mesmo endereço não é consultado duas vezes.

Precisão: "numero" (achou a casa), "rua" (achou só a rua) ou "bairro"/"cidade" — com raio de
caminhada de algumas centenas de metros, só "numero" e "rua" servem; o resto vai para revisão.
"""

import asyncio
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import settings

ARQUIVO = Path(__file__).resolve().parents[2] / "data" / "rotas.sqlite"
_trava = threading.Lock()
_vez = asyncio.Lock()
_ultima = 0.0
INTERVALO_NOMINATIM_S = 1.1
AGENTE = "SS-Telematica-Roteirizacao-Fretamento/1.0"
PRECISAS = ("numero", "rua")


def _con() -> sqlite3.Connection:
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS geocodificacao (chave TEXT PRIMARY KEY, resposta TEXT, motor TEXT, em REAL)")
    return c


def chave(endereco: str) -> str:
    return re.sub(r"\s+", " ", (endereco or "").strip().lower())


def _guardado(k: str) -> Optional[dict]:
    with _trava, _con() as c:
        r = c.execute("SELECT resposta FROM geocodificacao WHERE chave = ?", (k,)).fetchone()
    return json.loads(r["resposta"]) if r else None


def _guardar(k: str, resposta: dict, motor: str) -> None:
    with _trava, _con() as c:
        c.execute("INSERT OR REPLACE INTO geocodificacao VALUES (?, ?, ?, ?)", (k, json.dumps(resposta, ensure_ascii=False), motor, time.time()))


def motor() -> str:
    return "here" if getattr(settings, "HERE_API_KEY", "") else "nominatim"


def _precisao_nominatim(r: dict) -> str:
    t = (r.get("addresstype") or r.get("type") or "").lower()
    if "house_number" in (r.get("address") or {}) or t in ("house", "building", "residential_building"):
        return "numero"
    if t in ("road", "street", "residential", "pedestrian", "tertiary", "secondary", "primary", "service", "living_street"):
        return "rua"
    if t in ("suburb", "neighbourhood", "quarter", "city_district", "hamlet"):
        return "bairro"
    return "cidade"


async def _nominatim(q: str) -> Optional[dict]:
    global _ultima
    async with _vez:
        espera = INTERVALO_NOMINATIM_S - (time.monotonic() - _ultima)
        if espera > 0:
            await asyncio.sleep(espera)
        try:
            async with httpx.AsyncClient(timeout=20, headers={"User-Agent": AGENTE, "Accept-Language": "pt-BR"}) as c:
                r = await c.get("https://nominatim.openstreetmap.org/search",
                                params={"q": q, "format": "jsonv2", "countrycodes": "br", "limit": 1, "addressdetails": 1})
        finally:
            _ultima = time.monotonic()
    if r.status_code != 200:
        raise RuntimeError(f"OpenStreetMap respondeu {r.status_code}")
    js = r.json()
    if not js:
        return None
    x = js[0]
    return {"lat": float(x["lat"]), "lng": float(x["lon"]), "precisao": _precisao_nominatim(x), "achado": x.get("display_name")}


async def _here(q: str) -> Optional[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://geocode.search.hereapi.com/v1/geocode",
                        params={"q": q, "in": "countryCode:BRA", "limit": 1, "lang": "pt-BR", "apiKey": settings.HERE_API_KEY})
    if r.status_code != 200:
        raise RuntimeError(f"HERE respondeu {r.status_code}")
    itens = r.json().get("items") or []
    if not itens:
        return None
    x = itens[0]
    tipo = x.get("resultType", "")
    prec = {"houseNumber": "numero", "street": "rua", "intersection": "rua", "place": "numero"}.get(tipo, "bairro" if tipo == "locality" and x.get("localityType") == "district" else "cidade")
    p = x.get("position") or {}
    return {"lat": float(p["lat"]), "lng": float(p["lng"]), "precisao": prec, "achado": (x.get("address") or {}).get("label")}


def _sem_numero(q: str) -> str:
    # "Rua X, 123 - Bairro" → "Rua X - Bairro": quando a casa não está no mapa, ao menos a rua.
    return re.sub(r",?\s*(n[ºo°.]?\s*)?\d+[a-zA-Z]?\b", "", q, count=1).strip(" ,-")


async def localizar(endereco: str) -> dict:
    """{"lat","lng","precisao","achado"} ou {"erro"}; guarda o resultado (inclusive o não achado)."""
    k = chave(endereco)
    if not k:
        return {"erro": "endereço vazio"}
    hit = _guardado(k)
    if hit is not None:
        return {**hit, "guardado": True}
    busca = _here if motor() == "here" else _nominatim
    try:
        res = await busca(endereco)
        if res is None:
            alt = _sem_numero(endereco)
            if alt and alt.lower() != endereco.lower():
                res = await busca(alt)
                if res and res["precisao"] == "numero":
                    res["precisao"] = "rua"
    except Exception as e:  # rede ou limite do serviço: não guarda, para tentar de novo
        return {"erro": f"falha na busca ({e})"}
    out = res or {"erro": "endereço não encontrado no mapa"}
    _guardar(k, out, motor())
    return out
