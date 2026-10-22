"""Rota automática de fretamento (pedido do PM, 22/10/2026).

Entrada, por empresa contratante: o endereço da empresa (destino), o raio que ela aceita que o
funcionário caminhe até o ponto de embarque, os lugares por veículo e, opcional, o horário de
chegada. E a lista de funcionários já com coordenada.

1. Pontos de embarque — o menor número de pontos que deixa TODO funcionário a no máximo o raio a
   pé do seu ponto (cobertura gulosa: escolhe o lugar que atende mais gente ainda sem ponto, até
   cobrir todos). Depois cada ponto vai para o meio do seu grupo, se ninguém passar do raio.
   A pé = linha reta × FATOR_CAMINHADA (as ruas não são retas). Sem motor de rotas a pé, é estimativa.
2. Veículos — varredura por ângulo em volta da empresa, enchendo cada veículo até os lugares.
   Ponto com mais gente que um veículo é dividido.
3. Ordem das paradas — começa pela mais longe da empresa e vem se aproximando (vizinho mais
   próximo), melhorada com 2-opt, terminando na empresa. Km = linha reta × FATOR_ESTRADA, como no
   resto da roteirização enquanto o motor de rotas (TI) não estiver ligado.
"""

import math
from typing import Optional

from app.core import geo

FATOR_CAMINHADA = 1.25
FATOR_ESTRADA = 1.3
VELOCIDADE_KMH = 25.0  # ônibus de fretamento em área urbana, com trânsito
MIN_POR_PARADA = 1.0
MAX_FUNCIONARIOS = 3000


def _d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return geo.haversine_m(a[0], a[1], b[0], b[1])


def pontos_de_embarque(func: list[dict], raio_m: float) -> list[dict]:
    """[{lat, lng, membros: [índices], maior_caminhada_m}] cobrindo todos dentro do raio a pé."""
    n = len(func)
    if not n:
        return []
    alcance = raio_m / FATOR_CAMINHADA  # linha reta que cabe no raio a pé
    pos = [(f["lat"], f["lng"]) for f in func]
    # Vizinhos de cada um (candidato a ponto = a casa de alguém). Grade para não comparar todos com todos.
    passo = max(alcance / 111_000, 1e-4)
    grade: dict[tuple[int, int], list[int]] = {}
    for i, (la, ln) in enumerate(pos):
        grade.setdefault((int(la // passo), int(ln // passo)), []).append(i)
    viz: list[list[int]] = []
    for i, (la, ln) in enumerate(pos):
        gi, gj = int(la // passo), int(ln // passo)
        perto = []
        for di in (-1, 0, 1):
            for dj in (-2, -1, 0, 1, 2):  # longitude encolhe menos que 2× no Brasil
                for j in grade.get((gi + di, gj + dj), ()):
                    if _d(pos[i], pos[j]) <= alcance:
                        perto.append(j)
        viz.append(perto)
    # Quantos ainda sem ponto cada candidato atende (vizinhança é simétrica: atualiza ao cobrir).
    cont = [len(v) for v in viz]
    coberto = [False] * n
    faltam = n
    escolhidos: list[int] = []
    while faltam:
        melhor = max(range(n), key=lambda c: (cont[c], -c))
        escolhidos.append(melhor)
        for j in viz[melhor]:
            if not coberto[j]:
                coberto[j] = True
                faltam -= 1
                for c in viz[j]:
                    cont[c] -= 1
    # Cada funcionário no ponto escolhido mais perto (todos têm ao menos um dentro do alcance).
    centros = [pos[c] for c in escolhidos]
    membros: list[list[int]] = [[] for _ in escolhidos]
    for i in range(n):
        k = min(range(len(centros)), key=lambda k: _d(pos[i], centros[k]))
        membros[k].append(i)
    pontos = []
    for c, ms in zip(centros, membros):
        if not ms:
            continue
        meio = (sum(pos[i][0] for i in ms) / len(ms), sum(pos[i][1] for i in ms) / len(ms))
        if all(_d(pos[i], meio) <= alcance for i in ms):
            c = meio
        maior = max(_d(pos[i], c) for i in ms) * FATOR_CAMINHADA
        pontos.append({"lat": c[0], "lng": c[1], "membros": ms, "maior_caminhada_m": round(maior)})
    return pontos


def _dividir(pontos: list[dict], lugares: int) -> list[dict]:
    """Ponto com mais gente que um veículo vira vários (mesmo lugar, grupos de até `lugares`)."""
    out = []
    for p in pontos:
        ms = p["membros"]
        for i in range(0, len(ms), lugares):
            out.append({**p, "membros": ms[i:i + lugares]})
    return out


def _ordem(paradas: list[dict], empresa: tuple[float, float]) -> list[dict]:
    if len(paradas) <= 1:
        return paradas
    pos = [(p["lat"], p["lng"]) for p in paradas]
    resto = set(range(len(pos)))
    atual = max(resto, key=lambda i: _d(pos[i], empresa))
    seq = [atual]
    resto.remove(atual)
    while resto:
        atual = min(resto, key=lambda i: _d(pos[seq[-1]], pos[i]))
        seq.append(atual)
        resto.remove(atual)
    pts = [pos[i] for i in seq] + [empresa]

    def custo(s):
        return sum(_d(pts[s[k]], pts[s[k + 1]]) for k in range(len(s) - 1))

    idx = list(range(len(pts)))  # o último (empresa) fica fixo
    melhorou = True
    while melhorou:
        melhorou = False
        for i in range(0, len(idx) - 2):
            for j in range(i + 1, len(idx) - 1):
                novo = idx[:i] + idx[i:j + 1][::-1] + idx[j + 1:]
                if custo(novo) + 1 < custo(idx):
                    idx, melhorou = novo, True
    return [paradas[seq[k]] for k in idx[:-1]]


def _rotas(pontos: list[dict], empresa: tuple[float, float], lugares: int) -> list[list[dict]]:
    """Varredura por ângulo: enche um veículo, passa ao próximo."""
    ang = sorted(pontos, key=lambda p: math.atan2(p["lat"] - empresa[0], p["lng"] - empresa[1]))
    if not ang:
        return []
    # Começa a volta no maior vão entre ângulos, para não partir um grupo vizinho em dois veículos.
    a = [math.atan2(p["lat"] - empresa[0], p["lng"] - empresa[1]) for p in ang]
    vaos = [(a[(i + 1) % len(a)] - a[i]) % (2 * math.pi) for i in range(len(a))]
    ini = (max(range(len(vaos)), key=lambda i: vaos[i]) + 1) % len(a)
    ang = ang[ini:] + ang[:ini]
    rotas, atual, ocup = [], [], 0
    for p in ang:
        q = len(p["membros"])
        if atual and ocup + q > lugares:
            rotas.append(atual)
            atual, ocup = [], 0
        atual.append(p)
        ocup += q
    if atual:
        rotas.append(atual)
    return rotas


def _hora(h: Optional[str], menos_min: float) -> Optional[str]:
    if not h or ":" not in h:
        return None
    hh, mm = h.split(":")[:2]
    t = (int(hh) * 60 + int(mm) - round(menos_min)) % (24 * 60)
    return f"{t // 60:02d}:{t % 60:02d}"


def montar(func: list[dict], empresa: dict, raio_m: float, lugares: int, chegada: Optional[str] = None) -> dict:
    """Pontos de embarque e rotas de uma empresa. `func` = [{nome, lat, lng, ...}]."""
    if len(func) > MAX_FUNCIONARIOS:
        raise ValueError(f"Mais de {MAX_FUNCIONARIOS} funcionários numa empresa.")
    dest = (empresa["lat"], empresa["lng"])
    pontos = _dividir(pontos_de_embarque(func, raio_m), max(1, lugares))
    rotas_out = []
    for n, grupo in enumerate(_rotas(pontos, dest, max(1, lugares)), start=1):
        seq = _ordem(grupo, dest)
        pts = [(p["lat"], p["lng"]) for p in seq] + [dest]
        km_trecho = [_d(pts[k], pts[k + 1]) / 1000 * FATOR_ESTRADA for k in range(len(pts) - 1)]
        km_total = sum(km_trecho)
        min_total = km_total / VELOCIDADE_KMH * 60 + MIN_POR_PARADA * len(seq)
        # Horário de cada embarque contando de trás para frente a partir da chegada na empresa.
        falta = min_total
        paradas = []
        for k, p in enumerate(seq):
            paradas.append({
                "ordem": k + 1, "lat": round(p["lat"], 6), "lng": round(p["lng"], 6),
                "horario": _hora(chegada, falta),
                "maior_caminhada_m": p["maior_caminhada_m"],
                "funcionarios": [{"indice": func[i].get("indice", i), "nome": func[i].get("nome"),
                                  "caminhada_m": round(_d((func[i]["lat"], func[i]["lng"]), (p["lat"], p["lng"])) * FATOR_CAMINHADA)}
                                 for i in p["membros"]],
            })
            falta -= km_trecho[k] / VELOCIDADE_KMH * 60 + MIN_POR_PARADA
        rotas_out.append({
            "rota": n, "passageiros": sum(len(p["funcionarios"]) for p in paradas), "lugares": lugares,
            "km": round(km_total, 1), "minutos": round(min_total), "saida": _hora(chegada, min_total),
            "paradas": paradas, "caminho": [[round(a, 6), round(b, 6)] for a, b in pts],
        })
    return {
        "pontos": sum(len(r["paradas"]) for r in rotas_out), "funcionarios": len(func), "veiculos": len(rotas_out),
        "km_total": round(sum(r["km"] for r in rotas_out), 1), "raio_m": raio_m, "rotas": rotas_out,
        "criterio": {"fator_caminhada": FATOR_CAMINHADA, "fator_estrada": FATOR_ESTRADA, "velocidade_kmh": VELOCIDADE_KMH,
                     "min_por_parada": MIN_POR_PARADA},
    }
