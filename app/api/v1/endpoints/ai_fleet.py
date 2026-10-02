"""
IA Ops Advisor (antigo AI Fleet Manager) — leitura do painel de decisão.

Nenhuma regra de negócio aqui, de propósito: tudo é calculado pelas funções
do banco `fleet_mvp.*` e `fleet_ai.*`, gravadas pelo worker
`ss-worker-fleet-insights`. A mesma `kpis_periodo` alimenta a tela e o texto
da IA, para os dois nunca discordarem (vault: indicadores-ai-ops-advisor).

Regras de acesso e de período (vault A2 e A11):
- só contas do acesso do usuário (interno SS / master vê todas);
- janela máxima de 92 dias;
- comparação padrão: a mesma janela 3 meses antes.

O usuário de banco precisa de USAGE nos esquemas `fleet_mvp` e `fleet_ai`.
Sem isso a rota responde 503 dizendo o motivo, em vez de um erro genérico.
"""

import asyncio
from datetime import date, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.database import AsyncSessionLocalReplica
from app.core.escopo import escopo_do_usuario
from app.middleware.auth import require_permission

router = APIRouter()

MAX_DIAS = 92
MESES_DE_REFERENCIA = 3

ROTULOS = {
    "pct_ideal": "Condução ideal",
    "pct_irregular": "Condução irregular",
    "km_por_litro": "Consumo médio",
    "pct_horas_nao_identificadas": "Horas sem condutor",
    "tempo_horas": "Horas por condutor",
    "distancia_km": "Km por condutor",
    "count_embreagem": "Embreagem excessiva",
    "count_freada_brusca": "Freada brusca",
    "count_aceleracao_brusca": "Aceleração brusca",
    "count_excesso_velocidade": "Excesso de velocidade",
    "pct_verde": "Faixa verde",
    "pct_inercia": "Inércia",
    "pct_extra_eco": "Extra econômica",
    "pct_ecoroll": "Eco-roll",
    "pct_baixa_velocidade": "Baixa velocidade",
    "pct_marcha_lenta": "Marcha lenta (parado ligado)",
    "pct_parado_ligado_produtivo": "Parado ligado produtivo",
    "pct_batendo_transmissao": "Batendo transmissão",
    "pct_amarela": "Faixa amarela",
    "pct_vermelha": "Faixa vermelha",
    "pct_parado_acelerando": "Parado acelerando",
    "pct_banguela": "Banguela",
    "pct_tolerancia": "Tolerância",
}

FAIXAS = {
    "verde": "pct_verde", "inercia": "pct_inercia", "extra_eco": "pct_extra_eco", "ecoroll": "pct_ecoroll",
    "baixa_velocidade": "pct_baixa_velocidade", "marcha_lenta": "pct_marcha_lenta",
    "parado_ligado_produtivo": "pct_parado_ligado_produtivo", "batendo_transmissao": "pct_batendo_transmissao",
    "amarela": "pct_amarela", "vermelha": "pct_vermelha", "parado_acelerando": "pct_parado_acelerando",
    "banguela": "pct_banguela", "tolerancia": "pct_tolerancia",
}


def _num(v: Any) -> Any:
    """Decimal do banco vira float no JSON; o resto passa como está."""
    if v is None or isinstance(v, (bool, int, str)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return v.isoformat() if hasattr(v, "isoformat") else v


def _linha(r) -> dict:
    return {k: _num(v) for k, v in dict(r).items()}


async def _ler(sql: str, params: dict) -> list[dict]:
    """Cada função numa sessão própria, para rodarem juntas."""
    async with AsyncSessionLocalReplica() as s:
        return [_linha(r) for r in (await s.execute(text(sql), params)).mappings().all()]


def _sem_acesso_ao_esquema(e: Exception) -> bool:
    return "permission denied for schema" in str(e) or "InsufficientPrivilege" in type(getattr(e, "orig", e)).__name__


def _erro_esquema() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "O usuário do banco desta API não tem acesso aos esquemas fleet_mvp/fleet_ai do IA Ops Advisor. "
        "É preciso conceder leitura (USAGE e EXECUTE) a esse usuário.",
    )


def _grupos_do_usuario(user) -> Optional[set[int]]:
    """None = todas (master/interno SS)."""
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return None
    grupos, _ = escopo_do_usuario(user)
    return {g for g in grupos if g and g > 0}


def _menos_meses(d: date, meses: int) -> date:
    m = d.month - meses
    a = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(a, m, dia)
        except ValueError:
            continue
    return date(a, m, 28)


@router.get("/contas")
async def contas(current_user=Depends(require_permission("reports", "read"))):
    """Contas com painel habilitado, dentro do acesso do usuário."""
    try:
        linhas = await _ler(
            'SELECT c.*, g.name AS _nome FROM fleet_mvp.conta c LEFT JOIN mova."group" g ON g.id = c.group_id',
            {},
        )
    except (ProgrammingError, DBAPIError) as e:
        if _sem_acesso_ao_esquema(e):
            raise _erro_esquema() from e
        raise
    permitidos = _grupos_do_usuario(current_user)
    saida = []
    for r in linhas:
        # Só as habilitadas (vault A2). O nome da coluna é lido com tolerância.
        habilitada = next((r[k] for k in ("habilitada", "ativa", "ativo", "enabled") if k in r), True)
        if not habilitada or (permitidos is not None and r["group_id"] not in permitidos):
            continue
        saida.append({"group_id": r["group_id"], "nome": r.get("nome") or r.get("_nome")})
    return sorted(saida, key=lambda x: (x["nome"] or "").lower())


@router.get("/painel")
async def painel(
    group_id: int = Query(...),
    inicio: date = Query(...),
    fim: date = Query(...),
    comparar: bool = Query(False, description="Compara com a mesma janela 3 meses antes"),
    ref_inicio: Optional[date] = Query(None),
    ref_fim: Optional[date] = Query(None),
    current_user=Depends(require_permission("reports", "read")),
):
    if fim < inicio:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "fim anterior a inicio")
    if (fim - inicio).days + 1 > MAX_DIAS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Janela máxima de {MAX_DIAS} dias")
    permitidos = _grupos_do_usuario(current_user)
    if permitidos is not None and group_id not in permitidos:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso a esta conta")

    p = {"g": group_id, "i": inicio, "f": fim}
    ri = ref_inicio or _menos_meses(inicio, MESES_DE_REFERENCIA)
    rf = ref_fim or _menos_meses(fim, MESES_DE_REFERENCIA)
    pr = {**p, "ri": ri, "rf": rf}

    consultas = {
        "frota": ("SELECT * FROM fleet_mvp.frota_periodo(:g, :i, :f)", p),
        "cadastro": ("SELECT * FROM fleet_mvp.cadastro_periodo(:g, :i, :f)", p),
        "kpis": ("SELECT * FROM fleet_mvp.kpis_periodo(:g, :i, :f) ORDER BY ordem", p),
        "acoes": ("SELECT * FROM fleet_mvp.acoes_periodo(:g, :i, :f) ORDER BY posicao", p),
        "tipico": ("SELECT * FROM fleet_mvp.condutor_tipico_periodo(:g, :i, :f)", p),
        "abaixo": ("SELECT * FROM fleet_mvp.condutores_abaixo_benchmark(:g, :i, :f)", p),
        "acima": ("SELECT * FROM fleet_mvp.condutores_acima_benchmark(:g, :i, :f)", p),
        "eventos": ("SELECT * FROM fleet_mvp.ranking_eventos(:g, :i, :f)", p),
        "faixas": ("SELECT * FROM fleet_mvp.ranking_faixas(:g, :i, :f)", p),
        "uni_sem": ("SELECT * FROM fleet_mvp.unidades_sem_sinal_faixa(:g, :i, :f)", p),
        "cond_sem": ("SELECT * FROM fleet_mvp.condutores_sem_sinal_faixa(:g, :i, :f)", p),
        "carga": ("SELECT * FROM fleet_mvp.carga_status(:g)", {"g": group_id}),
    }
    if comparar:
        consultas["comparacao"] = ("SELECT * FROM fleet_mvp.kpis_comparacao(:g, :i, :f, :ri, :rf)", pr)
        consultas["economia"] = ("SELECT * FROM fleet_mvp.economia_combustivel(:g, :i, :f, :ri, :rf)", pr)

    try:
        res = dict(zip(consultas, await asyncio.gather(*(_ler(sql, par) for sql, par in consultas.values()))))
    except (ProgrammingError, DBAPIError) as e:
        if _sem_acesso_ao_esquema(e):
            raise _erro_esquema() from e
        raise

    # O texto da IA é opcional (vault A1: sem insight, os indicadores aparecem
    # mesmo assim). Sem permissão no esquema fleet_ai, o painel segue sem ele.
    insight_indisponivel = None
    try:
        res["insight"] = await _ler("SELECT * FROM fleet_ai.ultimo_insight(:g, :f)", {"g": group_id, "f": fim})
    except (ProgrammingError, DBAPIError) as e:
        if not _sem_acesso_ao_esquema(e):
            raise
        res["insight"] = []
        insight_indisponivel = "sem_permissao"

    frota = (res["frota"] or [{}])[0]
    if not frota or not (frota.get("dias") or 0) or ((frota.get("dias") or 0) - (frota.get("dias_sem_dado") or 0)) <= 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sem dado carregado para esta conta no período")
    cad = (res["cadastro"] or [{}])[0]
    comp = {c["chave"]: c for c in res.get("comparacao", [])}

    kpis = []
    for k in res["kpis"]:
        c = comp.get(k["chave"])
        kpis.append(
            {
                "chave": k["chave"],
                "rotulo": ROTULOS.get(k["chave"], k["chave"]),
                "valor": k.get("valor"),
                "unidade": k.get("unidade"),
                "escopo": k.get("escopo"),
                "veredito": k.get("status"),
                "suprimido_por": k.get("status_suprimido_por"),
                "desvio": k.get("desvio"),
                "desvio_relativo": k.get("desvio_relativo_pct"),
                "ordem": k.get("ordem"),
                "confianca": k.get("confianca"),
                "meta": (
                    {"valor": k["meta_valor"], "tipo": k.get("meta_tipo"), "origem": k.get("meta_origem"),
                     "fonte": k.get("meta_fonte"), "base": k.get("meta_base")}
                    if k.get("meta_valor") is not None else None
                ),
                "comparacao": (
                    {"inicio": ri.isoformat(), "fim": rf.isoformat(), "valor_referencia": c.get("valor_ref"),
                     "variacao": c.get("delta_pct"), "direcao": c.get("direcao"), "comparavel": c.get("comparavel"),
                     "motivo": c.get("motivo")}
                    if c else None
                ),
            }
        )

    acoes = [
        {
            "chave": a["chave"],
            "rotulo": ROTULOS.get(a["chave"], a["chave"]),
            "severidade": a.get("severidade"),
            "categoria": a.get("categoria"),
            "desvio_relativo": a.get("desvio_relativo_pct"),
            # A tela mostra um texto só: o porquê seguido da recomendação.
            "texto": " ".join(x for x in (a.get("porque"), a.get("recomendacao")) if x) or None,
            "porque": a.get("porque"),
            "recomendacao": a.get("recomendacao"),
            "efeito_esperado": a.get("efeito_esperado"),
            "prazo_dias": a.get("prazo_dias"),
            "dono_sugerido": a.get("dono_sugerido"),
            "valor": a.get("valor"),
            "unidade": a.get("unidade"),
            "meta_valor": a.get("meta_valor"),
            "criterio": a.get("criterio"),
        }
        for a in res["acoes"]
    ]

    tip = (res["tipico"] or [None])[0]
    eco = (res.get("economia") or [None])[0]
    ins = (res["insight"] or [None])[0]
    dias = (fim - inicio).days + 1
    mes_completo = inicio.day == 1 and (fim + timedelta(days=1)).day == 1 and inicio.month == fim.month

    return {
        "group_id": group_id,
        "grupo_nome": cad.get("nome"),
        "grupo_razao_social": cad.get("razao_social"),
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat(), "dias": dias, "mes_completo": mes_completo},
        "kpis": kpis,
        "acoes_ordenadas": acoes,
        "frota": {
            "veiculos": cad.get("veiculos_com_movimento"),
            "viagens": frota.get("viagens"),
            "distancia_km": frota.get("distancia_km"),
            "tempo_horas": frota.get("tempo_horas"),
            "km_por_litro": frota.get("km_por_litro"),
            "pct_ideal": frota.get("pct_ideal"),
            "pct_irregular": frota.get("pct_irregular"),
            "condutores_identificados": frota.get("qtd_condutores_identificados"),
            "benchmark_pct_ideal": frota.get("benchmark_pct_ideal"),
            "faixas_pct": {k: frota.get(c) for k, c in FAIXAS.items()},
            "eventos_total": {
                k: frota.get(f"{k}_total")
                for k in ("count_embreagem", "count_freada_brusca", "count_aceleracao_brusca", "count_excesso_velocidade")
            },
        },
        "condutor_tipico": (
            {"qtd_condutores": tip.get("qtd_condutores"), "distancia_km": tip.get("distancia_km"),
             "tempo_horas": tip.get("tempo_horas"), "faixas_pct": {k: tip.get(c) for k, c in FAIXAS.items()}}
            if tip else None
        ),
        "economia_combustivel": (
            {"inicio": ri.isoformat(), "fim": rf.isoformat(), "litros": eco.get("economia_litros"), "reais": None,
             "confiavel": eco.get("confiavel"), "motivo": eco.get("motivo"),
             "km_por_litro": eco.get("km_por_litro"), "km_por_litro_ref": eco.get("km_por_litro_ref")}
            if eco else None
        ),
        "condutores_abaixo_benchmark": res["abaixo"],
        "condutores_acima_benchmark": res["acima"],
        "ranking_eventos": res["eventos"],
        "ranking_faixas": res["faixas"],
        "unidades_sem_sinal_faixa": res["uni_sem"],
        "condutores_sem_sinal_faixa": res["cond_sem"],
        "cadastro": cad or None,
        "atualizacao": (res["carga"] or [None])[0],
        "insight": (
            {"id": ins.get("id"), "texto": ins.get("texto"), "blocos": ins.get("blocos"), "gerado_em": ins.get("criado_em")}
            if ins and ins.get("texto") else None
        ),
        "insight_indisponivel": insight_indisponivel,
        "data_quality": {
            "cobertura_combustivel_pct": frota.get("cobertura_combustivel_pct"),
            "confianca_combustivel": frota.get("confianca_combustivel"),
            "pct_horas_nao_identificadas": frota.get("pct_horas_nao_identificadas"),
            "condutores_identificados": frota.get("qtd_condutores_identificados"),
            "dias_sem_dado": frota.get("dias_sem_dado"),
            "metas_nao_validadas": [k["chave"] for k in res["kpis"] if k.get("status_suprimido_por") == "meta_nao_validada"],
        },
    }
