"""
Acessos à plataforma — uso por pessoa e por cliente. Exclusivo da SS
(usuário interno, `user_mova = 1`).

Fonte: `mova.session`, gravada pelo sistema antigo (`plataforma_web`,
authentication-class.php) a cada entrada ou renovação de sessão: usuário,
data e hora (hora de Brasília), IP e navegador. Só leitura.

Cuidados verificados nos dados (02/10/2026):
- ~95% das linhas são de INTEGRAÇÕES, não de pessoas: um robô "Synapse" que
  entra a cada 15 s, o Power BI ("Microsoft.Data.Mashup") e linhas sem
  navegador. Elas ficam separadas; o mapa de calor conta só pessoas.
- Pessoa = navegador de verdade ("Mozilla/5.0 …" que não seja robô).
- O sistema novo não grava nesta tabela: o uso dele ainda não aparece aqui.
- O IP não é devolvido para a tela (dado pessoal; não é preciso para o uso).
- A conta (`account_id`) não separa clientes — quase todos estão na 539.
  O cliente é o GRUPO, como no resto da plataforma: para cada usuário vale o
  grupo principal (o que tem mais linhas em `user_group_access`); quem tem
  acesso a mais de 20 grupos é tratado como "acesso a vários clientes".
- A tabela não tem índice por data. Para não varrer várias vezes, o banco
  devolve UMA agregação (usuário × dia × hora × aparelho) e o resto é somado
  aqui; o cadastro de usuários e o resultado ficam em memória.
"""

import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import get_current_user

router = APIRouter()

HUMANO = (
    "s.browser ILIKE 'Mozilla/5.0%' AND s.browser !~* "
    "'(bot|crawler|spider|headless|synapse|mashup|python|curl|postman|okhttp|java/|go-http)'"
)
DISPOSITIVO = (
    "CASE WHEN s.browser ~* '(ipad|tablet)' THEN 'tablet' "
    "WHEN s.browser ~* '(mobile|android|iphone)' THEN 'celular' ELSE 'computador' END"
)
NAVEGADOR = (
    "CASE WHEN s.browser ~* 'edg/' THEN 'Edge' WHEN s.browser ~* '(opr/|opera)' THEN 'Opera' "
    "WHEN s.browser ~* 'samsungbrowser' THEN 'Samsung' WHEN s.browser ~* 'firefox' THEN 'Firefox' "
    "WHEN s.browser ~* 'chrome|crios' THEN 'Chrome' WHEN s.browser ~* 'safari' THEN 'Safari' ELSE 'Outro' END"
)
INTEGRACAO = (
    "CASE WHEN s.browser ~* 'synapse' THEN 'Robô Synapse' WHEN s.browser ~* 'mashup' THEN 'Power BI' "
    "WHEN s.browser IS NULL OR s.browser = '' THEN 'Sem identificação' ELSE 'Outra integração' END"
)
VARIOS = "Acesso a vários clientes"
MAX_GRUPOS = 20
TTL = 600
_CACHE: dict[tuple, tuple[float, dict]] = {}
_USUARIOS: dict = {"em": 0.0, "dados": {}}


def _so_ss(user):
    if not getattr(user, "is_super_admin", False):
        raise HTTPException(403, "Tela exclusiva da equipe SS.")


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


def _periodo(inicio: Optional[date], fim: Optional[date]) -> tuple[date, date]:
    fim = fim or date.today()
    inicio = inicio or (fim - timedelta(days=29))
    if inicio > fim:
        inicio, fim = fim, inicio
    if (fim - inicio).days > 365:
        raise HTTPException(422, "Escolha um período de até 1 ano.")
    return inicio, fim


async def _usuarios() -> dict[int, dict]:
    """Cadastro + cliente (grupo principal) de cada usuário, em memória por 30 min."""
    if time.time() - _USUARIOS["em"] < 1800 and _USUARIOS["dados"]:
        return _USUARIOS["dados"]
    linhas = await _ler(
        """
        WITH gp AS (
          SELECT user_id, group_id, n_grupos FROM (
            SELECT user_id, group_id,
                   row_number() OVER (PARTITION BY user_id ORDER BY count(*) DESC, group_id) AS rn,
                   count(*) OVER (PARTITION BY user_id) AS n_grupos
            FROM mova.user_group_access GROUP BY user_id, group_id) x
          WHERE rn = 1)
        SELECT u.id, u.name, u.login, u.email, u.status, COALESCE(u.user_mova, 0) AS user_mova,
               gp.group_id, gp.n_grupos, g.name AS grupo
        FROM mova.users u LEFT JOIN gp ON gp.user_id = u.id LEFT JOIN mova."group" g ON g.id = gp.group_id
        """,
        {},
    )
    dados = {}
    for r in linhas:
        varios = (r["n_grupos"] or 0) > MAX_GRUPOS
        dados[r["id"]] = {
            "nome": (r["name"] or "").strip() or r["login"], "login": r["login"], "email": r["email"],
            "ativo": r["status"] == 1, "ss": r["user_mova"] == 1,
            "group_id": None if varios else r["group_id"],
            "empresa": VARIOS if varios else ((r["grupo"] or "").strip() or "Sem grupo"),
        }
    _USUARIOS.update(em=time.time(), dados=dados)
    return dados


def _dia(d) -> date:
    return d if isinstance(d, date) and not isinstance(d, datetime) else d.date()


@router.get("/painel")
async def painel(
    inicio: Optional[date] = Query(None),
    fim: Optional[date] = Query(None),
    group_id: Optional[int] = Query(None, description="Cliente (grupo principal do usuário)"),
    incluir_ss: bool = Query(False, description="Inclui os usuários internos da SS"),
    user=Depends(get_current_user),
):
    _so_ss(user)
    ini, fi = _periodo(inicio, fim)
    chave = ("painel", ini, fi, group_id, incluir_ss)
    if (c := _CACHE.get(chave)) and time.time() - c[0] < TTL:
        return c[1]

    dias = (fi - ini).days + 1
    ant_ini, ant_fim = ini - timedelta(days=dias), ini - timedelta(days=1)
    s0 = min(ant_ini, ini - timedelta(days=60))
    p = {"a": s0, "i": ini, "f": fi + timedelta(days=1)}

    usuarios = await _usuarios()
    agregado = await _ler(
        f"""SELECT s.users_id AS u, s.time::date AS dia, extract(isodow FROM s.time)::int AS dow,
                   extract(hour FROM s.time)::int AS hora, {DISPOSITIVO} AS disp, {NAVEGADOR} AS nav,
                   count(*) AS n, max(s.time) AS ultimo
            FROM mova.session s WHERE s.time >= :a AND s.time < :f AND {HUMANO}
            GROUP BY 1, 2, 3, 4, 5, 6""",
        p,
    )
    maquinas = await _ler(
        f"""SELECT s.users_id AS u, {INTEGRACAO} AS tipo, count(*) AS n, max(s.time) AS ultimo
            FROM mova.session s WHERE s.time >= :i AND s.time < :f AND NOT COALESCE({HUMANO}, false)
            GROUP BY 1, 2 ORDER BY 3 DESC""",
        p,
    )

    def entra(uid: int) -> bool:
        info = usuarios.get(uid)
        if not info:
            return False
        if not incluir_ss and info["ss"]:
            return False
        return not group_id or info["group_id"] == group_id

    matriz: dict[tuple, list] = defaultdict(lambda: [0, set()])
    por_dia: dict[date, list] = defaultdict(lambda: [0, set()])
    pess: dict[int, dict] = {}
    disp: dict[tuple, list] = defaultdict(lambda: [0, set()])
    antes: dict[int, dict] = {}
    ant_total = [0, set()]

    for r in agregado:
        uid = r["u"]
        if not entra(uid):
            continue
        d = _dia(r["dia"])
        n = r["n"]
        if ini <= d <= fi:
            m = matriz[(r["dow"], r["hora"])]
            m[0] += n
            m[1].add(uid)
            pd = por_dia[d]
            pd[0] += n
            pd[1].add(uid)
            x = pess.setdefault(uid, {"acessos": 0, "dias": set(), "ultimo": None, "disp": Counter(), "nav": Counter()})
            x["acessos"] += n
            x["dias"].add(d)
            x["ultimo"] = max(filter(None, [x["ultimo"], r["ultimo"]]))
            x["disp"][r["disp"]] += n
            x["nav"][r["nav"]] += n
            k = disp[(r["disp"], r["nav"])]
            k[0] += n
            k[1].add(uid)
        else:
            if ant_ini <= d <= ant_fim:
                ant_total[0] += n
                ant_total[1].add(uid)
            if d < ini:
                b = antes.setdefault(uid, {"dias": set(), "ultimo": None})
                b["dias"].add(d)
                b["ultimo"] = max(filter(None, [b["ultimo"], r["ultimo"]]))

    pessoas = []
    for uid, x in pess.items():
        info = usuarios[uid]
        pessoas.append({
            "user_id": uid, "nome": info["nome"], "login": info["login"], "group_id": info["group_id"],
            "empresa": info["empresa"], "acessos": x["acessos"], "dias": len(x["dias"]), "ultimo": x["ultimo"],
            "dispositivo": x["disp"].most_common(1)[0][0], "navegador": x["nav"].most_common(1)[0][0], "ss": info["ss"],
        })
    pessoas.sort(key=lambda r: (-r["dias"], -r["acessos"]))

    sumidos = [
        {"user_id": uid, "nome": usuarios[uid]["nome"], "login": usuarios[uid]["login"], "empresa": usuarios[uid]["empresa"],
         "ultimo": b["ultimo"], "dias_antes": len(b["dias"])}
        for uid, b in antes.items()
        if uid not in pess and usuarios[uid]["ativo"] and (ini - max(b["dias"])).days <= 60
    ]
    sumidos.sort(key=lambda r: -r["dias_antes"])

    # Adoção: quantas pessoas ativas de cada cliente usaram no período.
    cadastrados = Counter(
        info["group_id"] for uid, info in usuarios.items()
        if info["ativo"] and info["group_id"] and (incluir_ss or not info["ss"]) and (not group_id or info["group_id"] == group_id)
    )
    empresas: dict = {}
    for r in pessoas:
        e = empresas.setdefault(r["group_id"], {"group_id": r["group_id"], "nome": r["empresa"], "pessoas": 0,
                                                "acessos": 0, "dias_pessoa": 0, "ultimo": None})
        e["pessoas"] += 1
        e["acessos"] += r["acessos"]
        e["dias_pessoa"] += r["dias"]
        e["ultimo"] = max(filter(None, [e["ultimo"], r["ultimo"]]))
    for e in empresas.values():
        e["cadastrados"] = cadastrados.get(e["group_id"]) if e["group_id"] else None
        e["adocao_pct"] = round(100 * e["pessoas"] / e["cadastrados"], 1) if e["cadastrados"] else None
        e["dias_por_pessoa"] = round(e["dias_pessoa"] / e["pessoas"], 1) if e["pessoas"] else 0

    acessos = sum(r["acessos"] for r in pessoas)
    dias_pessoa = sum(r["dias"] for r in pessoas)
    integracoes = [
        {"user_id": r["u"], "nome": usuarios.get(r["u"], {}).get("nome", f"Usuário {r['u']}"),
         "empresa": usuarios.get(r["u"], {}).get("empresa", "—"), "tipo": r["tipo"], "acessos": r["n"], "ultimo": r["ultimo"]}
        for r in maquinas
        if r["u"] in usuarios and (not group_id or usuarios[r["u"]]["group_id"] == group_id)
    ][:50]

    saida = {
        "periodo": {"inicio": ini.isoformat(), "fim": fi.isoformat(), "dias": dias,
                    "anterior": {"inicio": ant_ini.isoformat(), "fim": ant_fim.isoformat()}},
        "totais": {
            "pessoas": len(pessoas), "acessos": acessos, "dias_pessoa": dias_pessoa,
            "empresas": len({r["group_id"] for r in pessoas if r["group_id"]}),
            "acessos_por_pessoa_dia": round(acessos / dias_pessoa, 1) if dias_pessoa else None,
            "dias_por_pessoa": round(dias_pessoa / len(pessoas), 1) if pessoas else None,
            "anterior": {"pessoas": len(ant_total[1]), "acessos": ant_total[0]},
            "sumidos": len(sumidos),
        },
        "matriz": [{"dia": k[0], "hora": k[1], "acessos": v[0], "pessoas": len(v[1])} for k, v in matriz.items()],
        "por_dia": [{"dia": d.isoformat(), "acessos": v[0], "pessoas": len(v[1])} for d, v in sorted(por_dia.items())],
        "pessoas": pessoas[:2000],
        "empresas": sorted(empresas.values(), key=lambda e: (-e["pessoas"], -e["acessos"])),
        "dispositivos": [{"dispositivo": k[0], "navegador": k[1], "acessos": v[0], "pessoas": len(v[1])} for k, v in disp.items()],
        "integracoes": integracoes,
        "sumidos": sumidos[:300],
    }
    _CACHE[chave] = (time.time(), saida)
    return saida


@router.get("/pessoa/{user_id}")
async def pessoa(
    user_id: int,
    inicio: Optional[date] = Query(None),
    fim: Optional[date] = Query(None),
    user=Depends(get_current_user),
):
    _so_ss(user)
    ini, fi = _periodo(inicio, fim)
    info = (await _usuarios()).get(user_id)
    if not info:
        raise HTTPException(404, "Usuário não encontrado.")
    p = {"u": user_id, "i": ini, "f": fi + timedelta(days=1)}
    linhas = await _ler(
        f"""SELECT s.time AS em, {DISPOSITIVO} AS dispositivo, {NAVEGADOR} AS navegador
            FROM mova.session s WHERE s.users_id = :u AND s.time >= :i AND s.time < :f AND {HUMANO}
            ORDER BY s.time DESC""",
        p,
    )
    matriz: Counter = Counter()
    por_dia: Counter = Counter()
    for r in linhas:
        matriz[(r["em"].isoweekday(), r["em"].hour)] += 1
        por_dia[r["em"].date()] += 1
    return {
        "usuario": {"id": user_id, "nome": info["nome"], "login": info["login"], "email": info["email"],
                    "empresa": info["empresa"], "ativo": info["ativo"]},
        "matriz": [{"dia": k[0], "hora": k[1], "acessos": v} for k, v in matriz.items()],
        "por_dia": [{"dia": d.isoformat(), "acessos": v} for d, v in sorted(por_dia.items())],
        "ultimos": [dict(r) for r in linhas[:40]],
    }
