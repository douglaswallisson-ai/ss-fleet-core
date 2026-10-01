"""
Baixa o relevo (SRTM, dados públicos da NASA na base aberta "Terrain Tiles"
da AWS) só dos quadrados de 1° por onde a frota passou, e grava em 90 m.

Uso: python scripts/baixar_relevo.py [dias]   (padrão: 60 dias de posições)

Cada quadrado vem com 3601 × 3601 pontos (~30 m). Gravamos um a cada três
(1201 × 1201, ~90 m): as posições chegam a cada 1–2 km, então a resolução
maior só ocuparia disco (8 GB contra ~1 GB). Só lê o banco.
"""
import array
import asyncio
import gzip
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from app.core.database import AsyncSessionLocalReplica  # noqa: E402

PASTA = Path(__file__).resolve().parents[1] / "data" / "relevo"
URL = "https://s3.amazonaws.com/elevation-tiles-prod/skadi/{ns}/{nome}.hgt.gz"


def nome_quadrado(lat: int, lon: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


async def quadrados(dias: int) -> list[tuple[int, int]]:
    desde = datetime.now() - timedelta(days=dias)
    async with AsyncSessionLocalReplica() as db:
        # Uma posição por minuto 7 de cada hora basta para achar os quadrados.
        r = await db.execute(
            text(
                """
                SELECT DISTINCT floor(latitude)::int, floor(longitude)::int
                FROM mova.dev_status_30
                WHERE local_time >= :desde AND gps AND latitude <> 0 AND longitude <> 0
                  AND latitude BETWEEN -60 AND 60 AND extract(minute FROM local_time) = 7
                """
            ),
            {"desde": desde},
        )
        return sorted({(a, b) for a, b in r.all()})


def baixar(q: tuple[int, int]) -> str:
    lat, lon = q
    nome = nome_quadrado(lat, lon)
    destino = PASTA / f"{nome}.h3"
    if destino.exists():
        return "já tinha"
    try:
        with urllib.request.urlopen(URL.format(ns=nome[:3], nome=nome), timeout=120) as resp:
            bruto = gzip.decompress(resp.read())
    except urllib.error.HTTPError as e:
        return "sem relevo (mar)" if e.code in (403, 404) else f"erro {e.code}"
    n = 3601 if len(bruto) == 3601 * 3601 * 2 else 1201
    a = array.array("h")
    a.frombytes(bruto)
    a.byteswap()  # HGT é big-endian
    passo = (n - 1) // 1200
    saida = array.array("h")
    for linha in range(0, n, passo):
        saida.extend(a[linha * n : (linha + 1) * n : passo])
    tmp = destino.with_suffix(".tmp")
    tmp.write_bytes(saida.tobytes())
    tmp.replace(destino)
    return "ok"


async def main():
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    PASTA.mkdir(parents=True, exist_ok=True)
    lista = await quadrados(dias)
    print(f"{len(lista)} quadrados", flush=True)
    with ThreadPoolExecutor(max_workers=4) as ex:
        for i, (q, res) in enumerate(zip(lista, ex.map(baixar, lista)), 1):
            print(f"{i}/{len(lista)} {nome_quadrado(*q)} {res}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
