"""
Câmeras ao vivo — lista os veículos com câmera e abre o vídeo de um canal.

Fonte e regras: a tela "Vídeos Online" da plataforma de câmeras
(`plataforma_web_new`, `src/views/pages/cam/VideoOnline`, lida em 05/10/2026):
- veículo com câmera = vínculo ativo em `vcms.vcms_unit_device` (status 1, sem
  `release_date`); o código do equipamento é `mova.device.identifier`;
- o modelo decide o caminho do vídeo:
  - 122 (JIMI JC450): proxy JIMI (`/device/status/online`, `/openlivestream`).
    A URL volta em HTTP na porta 8881 e é trocada pelo CloudFront HTTPS (o
    navegador bloqueia HTTP dentro de HTTPS e a porta 8881 corta em 30 s);
  - 156/157 (Hikvision G40 / G40 PRO): proxy da HAT Cloud (`device/status/`,
    `device/preview/` com substream 1 e HLS). Status 4 = equipamento dormindo
    (ignição desligada);
  - os demais (MV03, 169 veículos em 05/10/2026) não têm vídeo ao vivo nem na
    plataforma de câmeras;
- canais 1 a 5, abertos um a um por escolha do operador (consumo de dados do
  chip do equipamento).

Correção: a plataforma de câmeras chamava os proxies direto do navegador, com a
chave da Hikvision dentro do código do front. Aqui o navegador fala só com este
backend: a chave fica no `.env`, e o acesso ao veículo é conferido pelo grupo
do usuário antes de abrir o vídeo.

Somente leitura no banco. Abrir o vídeo pede ao equipamento que transmita (é o
uso normal da plataforma de câmeras), mas não muda configuração nenhuma; os
comandos de configuração (ADAS/DMS) e o "STATUS" por SMS/GPRS ficam de fora.
SUPOSIÇÃO: a posição/ignição do `mova.dev_status` é a do próprio veículo (o
JC450 também é rastreador). Confirmar com o TI se há carro com rastreador e
câmera separados mostrando posições diferentes.
"""

import json
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocalReplica
from app.core.logging import get_logger
from app.middleware.auth import require_permission

logger = get_logger(__name__)
router = APIRouter()

MODELO_JIMI = {122}
MODELO_HIKVISION = {156, 157}
CANAIS = 5
STATUS_CACHE_S = 60
TIMEOUT_S = 20
#: Códigos de status da HAT Cloud que têm explicação para o operador.
HIK_STATUS = {4: "Equipamento em modo de espera (ignição desligada). Tente com o veículo em operação."}

_CACHE_STATUS: dict[tuple, tuple[float, dict]] = {}

SQL_VEICULOS = """
SELECT tu.id AS unit_id, tu.label AS placa, tu.label2 AS prefixo, tu.group_id,
       d.identifier AS codigo, d.device_model_id AS modelo_id, dm.name AS modelo,
       s.local_time AS ultima_posicao, s.speed AS velocidade, s.ignition AS ignicao,
       s.latitude, s.longitude, s.address AS endereco, s.driver_name AS motorista,
       s.poi_name AS ponto, s.area_name AS cerca
FROM vcms.vcms_unit_device v
JOIN mova.tracked_unit tu ON tu.id = v.unit_id
JOIN mova.device d ON d.id = v.device_id
LEFT JOIN mova.device_model dm ON dm.id = d.device_model_id
LEFT JOIN mova.dev_status s ON s.unit_id = v.unit_id
WHERE v.status = 1 AND v.release_date IS NULL AND {filtro}
ORDER BY tu.label2 NULLS LAST, tu.label
"""


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


def _f(v):
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


def _tipo(modelo_id) -> str | None:
    if modelo_id in MODELO_JIMI:
        return "jimi"
    if modelo_id in MODELO_HIKVISION:
        return "hikvision"
    return None


def _desembrulhar(dados):
    """O proxy JIMI às vezes devolve o envelope da Lambda ({statusCode, body: "json"})."""
    if isinstance(dados, dict) and "statusCode" in dados and "body" in dados:
        corpo = dados["body"]
        if isinstance(corpo, str):
            try:
                return json.loads(corpo)
            except ValueError:
                return corpo
        return corpo
    return dados


def _jimi_cli() -> httpx.AsyncClient:
    if not settings.JIMI_PROXY_URL:
        raise HTTPException(503, "Integração de vídeo JIMI não configurada (JIMI_PROXY_URL no .env).")
    return httpx.AsyncClient(base_url=settings.JIMI_PROXY_URL.rstrip("/"), timeout=TIMEOUT_S)


def _hik_cli() -> httpx.AsyncClient:
    if not (settings.HIKVISION_API_URL and settings.HIKVISION_API_KEY):
        raise HTTPException(503, "Integração de vídeo Hikvision não configurada (HIKVISION_API_URL e HIKVISION_API_KEY no .env).")
    return httpx.AsyncClient(base_url=settings.HIKVISION_API_URL.rstrip("/"), timeout=TIMEOUT_S,
                             headers={"x-api-key": settings.HIKVISION_API_KEY})


async def _status_jimi(codigos: list[str]) -> dict[str, bool | None]:
    if not codigos or not settings.JIMI_PROXY_URL:
        return {}
    async with _jimi_cli() as cli:
        r = await cli.get("/device/status/online", params={"deviceImeis": ",".join(codigos)})
    dados = _desembrulhar(r.json()) or {}
    if dados.get("code") not in (0, None):
        # Ex. 05/10/2026: {"code": 1, "msg": "Failed to contact orchestrator"} — o
        # proxy está no ar, mas o orquestrador JIMI por trás dele não.
        raise ValueError(dados.get("msg") or "proxy JIMI respondeu com erro")
    return {str(d.get("deviceImei")): d.get("isOnline") is True for d in dados.get("devices") or []}


async def _status_hik(codigos: list[str]) -> dict[str, bool | None]:
    if not codigos or not (settings.HIKVISION_API_URL and settings.HIKVISION_API_KEY):
        return {}
    saida: dict[str, bool | None] = {}
    async with _hik_cli() as cli:
        for i in range(0, len(codigos), 100):
            lote = codigos[i:i + 100]
            r = await cli.get("/device/status/", params={"deviceCodeList": json.dumps(lote), "pageNo": "1", "pageSize": str(len(lote))})
            dados = r.json()
            if dados.get("success") and (dados.get("data") or {}).get("status") == 0:
                for c, st in ((dados["data"].get("data")) or {}).items():
                    saida[str(c)] = st == 1
    return saida


async def _status(tipo: str, codigos: list[str]) -> tuple[dict, str | None]:
    """Status online por equipamento (cache de 60 s). Falha no proxy não derruba a lista."""
    chave = (tipo, tuple(sorted(codigos)))
    hit = _CACHE_STATUS.get(chave)
    if hit and time.time() - hit[0] < STATUS_CACHE_S:
        return hit[1], None
    try:
        r = await (_status_jimi(codigos) if tipo == "jimi" else _status_hik(codigos))
    except (httpx.HTTPError, ValueError, KeyError, AttributeError, TypeError) as e:  # proxy fora do ar: a tela mostra "status desconhecido"
        logger.warning("camera_status_falhou", tipo=tipo, erro=str(e))
        return {}, f"Serviço de vídeo {tipo.upper()} indisponível agora ({e}). O vídeo ao vivo desses veículos pode não abrir."
    _CACHE_STATUS[chave] = (time.time(), r)
    return r, None


@router.get("/veiculos")
async def veiculos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Veículos do grupo com câmera, a última posição e se a câmera está online."""
    _grupo_ok(user, group_id)
    async with AsyncSessionLocalReplica() as db:
        rows = (await db.execute(text(SQL_VEICULOS.format(filtro="tu.group_id = :g")), {"g": group_id})).mappings().all()

    itens = []
    for r in rows:
        tipo = _tipo(r["modelo_id"])
        itens.append({
            **{k: _f(v) for k, v in r.items()},
            "ignicao": bool(r["ignicao"]) if r["ignicao"] is not None else None,
            "integracao": tipo,
            "ao_vivo": tipo is not None,
        })

    avisos = []
    configurado = {"jimi": bool(settings.JIMI_PROXY_URL),
                   "hikvision": bool(settings.HIKVISION_API_URL and settings.HIKVISION_API_KEY)}
    for tipo in ("jimi", "hikvision"):
        codigos = [i["codigo"] for i in itens if i["integracao"] == tipo and i["codigo"]]
        if not codigos:
            continue
        if not configurado[tipo]:
            avisos.append(f"Vídeo {tipo.upper()} ainda não configurado neste servidor.")
            continue
        st, aviso = await _status(tipo, codigos)
        if aviso:
            avisos.append(aviso)
        for i in itens:
            if i["integracao"] == tipo:
                i["online"] = st.get(str(i["codigo"]))
    for i in itens:
        i.setdefault("online", None)

    return {"veiculos": itens, "canais": CANAIS, "configurado": configurado, "avisos": avisos}


class PedidoAoVivo(BaseModel):
    unit_id: int
    canal: int = Field(ge=1, le=CANAIS)


@router.post("/ao-vivo")
async def ao_vivo(p: PedidoAoVivo, user=Depends(require_permission("reports", "read"))):
    """Abre o vídeo ao vivo de um canal. Devolve a URL e o formato (flv ou hls) para o player."""
    async with AsyncSessionLocalReplica() as db:
        r = (await db.execute(text(SQL_VEICULOS.format(filtro="tu.id = :u")), {"u": p.unit_id})).mappings().first()
    if not r:
        raise HTTPException(404, "Veículo sem câmera vinculada.")
    _grupo_ok(user, r["group_id"])
    tipo = _tipo(r["modelo_id"])
    if not tipo:
        raise HTTPException(422, f"O modelo {r['modelo'] or r['modelo_id']} não tem vídeo ao vivo.")
    logger.info("camera_ao_vivo", user_id=getattr(user, "id", None), unit_id=p.unit_id, canal=p.canal, tipo=tipo)

    try:
        if tipo == "jimi":
            async with _jimi_cli() as cli:
                resp = await cli.post("/openlivestream", json={"deviceImei": r["codigo"], "channel": str(p.canal)})
            d = _desembrulhar(resp.json()) or {}
            if not isinstance(d, dict) or d.get("code") != 0:
                msg = d.get("msg") or d.get("error") if isinstance(d, dict) else None
                raise HTTPException(502, f"A câmera não abriu o canal {p.canal}: {msg or 'sem resposta do equipamento'}.")
            url = d.get("url") or d.get("httpFlvUrl") or (d.get("orchestratorResult") or {}).get("http_flv_url")
            if not url:
                raise HTTPException(502, "O serviço de vídeo não devolveu o endereço da transmissão.")
            if ":8881" in url and settings.JIMI_STREAM_CDN:
                url = settings.JIMI_STREAM_CDN.rstrip("/") + url.split(":8881", 1)[1]
            return {"url": url, "formato": "flv", "canal": p.canal}

        async with _hik_cli() as cli:
            resp = await cli.get("/device/preview/", params={"deviceCode": r["codigo"], "channelNo": str(p.canal),
                                                             "streamType": "1", "streamProtocol": "2"})
        d = resp.json()
        dd = d.get("data") or {}
        if not d.get("success") or dd.get("status") != 0 or not dd.get("data"):
            raise HTTPException(502, HIK_STATUS.get(dd.get("status")) or dd.get("msg") or d.get("message")
                                or f"A câmera não abriu o canal {p.canal}.")
        return {"url": dd["data"], "formato": "hls", "canal": p.canal}
    except httpx.HTTPError as e:
        logger.warning("camera_ao_vivo_falhou", tipo=tipo, erro=str(e))
        raise HTTPException(502, "Serviço de vídeo fora do ar. Tente de novo em instantes.")
