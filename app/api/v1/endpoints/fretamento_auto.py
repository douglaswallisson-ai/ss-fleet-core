"""Rota automática de fretamento: planilha de funcionários → pontos de embarque e rotas.

Regras em `app/core/fretamento_auto.py`. Nada daqui grava a lista de funcionários: a planilha é
lida, devolvida à tela e descartada; só os endereços buscados no mapa ficam guardados (sem nome).
"""

import base64
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core import fretamento_auto as fa, geocodificador, planilha
from app.middleware.auth import require_permission

router = APIRouter()

MAX_ARQUIVO_BYTES = 5 * 1024 * 1024
MAX_ENDERECOS_POR_PEDIDO = 15


class PedidoPlanilha(BaseModel):
    nome_arquivo: str
    conteudo_base64: str


@router.post("/planilha")
async def ler_planilha(p: PedidoPlanilha, user=Depends(require_permission("reports", "read"))):
    """Linhas da planilha e as colunas reconhecidas (nome, endereço, número, bairro, cidade, UF, CEP, empresa)."""
    try:
        conteudo = base64.b64decode(p.conteudo_base64.split(",")[-1], validate=False)
    except Exception:
        raise HTTPException(400, "Arquivo ilegível.")
    if len(conteudo) > MAX_ARQUIVO_BYTES:
        raise HTTPException(413, "Arquivo maior que 5 MB.")
    try:
        linhas = planilha.ler(p.nome_arquivo, conteudo)
    except planilha.PlanilhaInvalida as e:
        raise HTTPException(422, str(e))
    cab = linhas[0]
    largura = max(len(r) for r in linhas)
    return {
        "cabecalho": cab + [""] * (largura - len(cab)),
        "colunas": planilha.detectar_colunas(cab),
        "linhas": [r + [""] * (largura - len(r)) for r in linhas[1:]],
    }


class PedidoGeo(BaseModel):
    enderecos: list[str] = Field(..., max_length=MAX_ENDERECOS_POR_PEDIDO)


@router.post("/geocodificar")
async def geocodificar(p: PedidoGeo, user=Depends(require_permission("reports", "read"))):
    """Coordenada de até 15 endereços por vez (a tela manda em lotes e mostra o andamento)."""
    out = []
    for e in p.enderecos:
        out.append({"endereco": e, **(await geocodificador.localizar(e))})
    return {"motor": geocodificador.motor(), "itens": out, "precisas": list(geocodificador.PRECISAS)}


class Funcionario(BaseModel):
    indice: int
    nome: Optional[str] = None
    lat: float
    lng: float


class Empresa(BaseModel):
    nome: str
    lat: float
    lng: float


class PedidoRota(BaseModel):
    empresa: Empresa
    raio_m: float = Field(800, ge=50, le=5000)
    lugares: int = Field(44, ge=1, le=100)
    chegada: Optional[str] = Field(None, description="HH:MM de chegada na empresa")
    funcionarios: list[Funcionario] = Field(..., min_length=1, max_length=fa.MAX_FUNCIONARIOS)


@router.post("/otimizar")
async def otimizar(p: PedidoRota, user=Depends(require_permission("reports", "read"))):
    """Pontos de embarque dentro do raio a pé e as rotas até a empresa, otimizadas."""
    return fa.montar([f.model_dump() for f in p.funcionarios], p.empresa.model_dump(), p.raio_m, p.lugares, p.chegada)
