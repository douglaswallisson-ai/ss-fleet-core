"""
Manutenção preventiva e corretiva com dados reais.

Decisões de produto (PM, 02/10/2026):
- Preventiva: o CLIENTE cadastra o plano (por tipo de veículo, por modelo ou
  por veículo), partindo de modelos prontos que a SS oferece como sugestão.
- Corretiva: nasce de ALERTA automático (sinal real fora do normal) e vira
  ORDEM DE SERVIÇO, acompanhada até fechar. Também dá para abrir OS à mão.

Fontes (só leitura no banco):
- Odômetro e horímetro atuais: regra única em `app/core/odometro.py` (a mesma de
  Veículos, do mapa e do plataforma_web). Nunca `tracked_unit.initial_odometer`
  (valor da instalação).
- Sinais do motor: colunas `can_*`, `voltage` do `dev_status` (última leitura),
  julgados pelas leituras das últimas 24 h (`dev_status_30`): uma leitura só
  não abre alerta, e sensor que manda valor impossível vira "sinal suspeito"
  (qualidade do dado), não defeito do veículo. Conferido na CECOTI em
  05/10/2026: 6 veículos com óleo "44 kPa" idêntico em centenas de leituras
  com o motor acima de 900 rpm (valor travado), ARLA pulando entre 0, 1, 2 e
  100% na mesma hora, e tensão de 0 V com o alternador carregando 28,6 V.

⚠️ ARMAZENAMENTO PROVISÓRIO: planos, serviços feitos e ordens de serviço
ficam num SQLite local (`data/manutencao.sqlite`) — o banco de produção é só
leitura neste projeto. A tabela definitiva é decisão da engenharia.

Limites dos alertas (configuráveis em LIMITES), pesquisa de 02/10/2026:
- temperatura e óleo: Cummins, guia de referência L9, boletim 5676573 (2024).
  Faixa normal do líquido de arrefecimento de 79–95 °C, máximo de 107 °C.
  Pressão mínima do óleo de 69 kPa em marcha lenta e 207 kPa na rotação nominal;
- bateria: um alternador de 24 V carrega entre 27 e 29 V. Em repouso, a
  bateria de 24 V tem 25,4 V carregada e 24,4 V com 50%. No sistema de 12 V, metade.
SUPOSIÇÃO: os limites de outras marcas (MWM, Mercedes, Scania, Volvo) são
parecidos. Para confirmar, comparar com o manual de cada motor da frota.
Pressão de óleo igual a 0 com o motor girando = veículo sem esse sensor (o
motor não gira sem óleo), não alerta.
O horímetro fica de fora dos vencimentos até a unidade ser confirmada
(mediana 193.629 — não é hora).
"""

import json
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.core.odometro import horimetro_h, odometro_km
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "manutencao.sqlite"
_trava = threading.Lock()

#: Antecedência para "vence em breve".
AVISO_KM = 1_000
AVISO_DIAS = 15
# Horímetro: `hourmeter`/`can_engine_hourmeter` vêm em MINUTOS (confirmado pelo PM, 02/10/2026).
AVISO_HORAS = 50

LIMITES = {
    # °C. Julgado pela mediana dos últimos 30 min com o motor ligado, não por uma leitura (07/10/2026):
    # com 96 °C numa leitura, 63 de 99 caminhões da CECOTI davam "Motor quente" em 10 dias — é o pico
    # normal sob carga destes motores (RVB-3E31: mediana diária 87–93 °C, pico 98–103 °C). Só 3 chegaram a 107.
    "temp_atencao": 100.0, "temp_critico": 107.0,
    "oleo_min_kpa": 69.0, "oleo_rpm_min": 600.0,            # kPa, mínimo Cummins em marcha lenta (avaliado entre 600 e 899 rpm)
    "rpm_ligado": 500.0,                                    # acima disso o alternador deve estar carregando
    "v24_carga_min": 26.0, "v24_atencao": 24.4, "v24_critico": 24.0,   # sistema 24 V (leitura > 18 V)
    "v12_carga_min": 13.0, "v12_atencao": 12.2, "v12_critico": 12.0,   # sistema 12 V
    # A tensão vem da alimentação do RASTREADOR, não do borne da bateria: há
    # queda de alguns décimos no chicote. Para não alertar no limite, os
    # alertas só disparam com folga (05/10/2026: 24,1–24,3 V em repouso e
    # 25,9 V carregando apareciam como problema).
    "v24_alerta_carga": 25.5, "v24_alerta_repouso": 24.0,   # alertas, com a folga
    "v12_alerta_carga": 12.8, "v12_alerta_repouso": 12.0,
    "arla_min": 10.0,                                       # %
}

# Modelos de plano oferecidos como SUGESTÃO (o cliente ajusta ao manual do fabricante).
MODELOS_PLANO = [
    {"id": "pesado", "nome": "Caminhão pesado (sugestão)", "categorias": [3, 7, 20, 21], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 30000, "dias": 180, "horas": 1000},
        {"servico": "Filtro de combustível", "km": 30000, "dias": 180, "horas": 1500},
        {"servico": "Filtro de ar", "km": 60000, "dias": 365},
        {"servico": "Revisão de freios", "km": 40000, "dias": 180},
        {"servico": "Lubrificação do chassi", "km": 10000, "dias": 60},
        {"servico": "Revisão geral", "km": 100000, "dias": 365},
    ]},
    {"id": "onibus", "nome": "Ônibus urbano (sugestão)", "categorias": [12, 22], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 20000, "dias": 120, "horas": 1000},
        {"servico": "Filtro de combustível", "km": 20000, "dias": 120, "horas": 1500},
        {"servico": "Filtro de ar", "km": 40000, "dias": 240},
        {"servico": "Revisão de freios", "km": 25000, "dias": 120},
        {"servico": "Ar-condicionado", "km": None, "dias": 90},
        {"servico": "Revisão geral", "km": 80000, "dias": 365},
    ]},
    {"id": "leve", "nome": "Carro, pickup ou van (sugestão)", "categorias": [1, 2, 10, 14, 15], "itens": [
        {"servico": "Troca de óleo do motor e filtro", "km": 10000, "dias": 180},
        {"servico": "Filtro de ar", "km": 20000, "dias": 365},
        {"servico": "Revisão de freios", "km": 20000, "dias": 365},
        {"servico": "Revisão geral", "km": 40000, "dias": 365},
    ]},
]

STATUS_OS = ("aberta", "em_andamento", "aguardando_peca", "concluida", "cancelada")


def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS plano (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, nome TEXT NOT NULL,
            escopo TEXT NOT NULL, alvo TEXT NOT NULL, itens TEXT NOT NULL, atualizado_em TEXT, atualizado_por INTEGER);
        CREATE TABLE IF NOT EXISTS servico (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, unit_id INTEGER NOT NULL,
            servico TEXT NOT NULL, data TEXT NOT NULL, odometro_km REAL, custo REAL, oficina TEXT, obs TEXT,
            ordem_id INTEGER, registrado_em TEXT, registrado_por INTEGER);
        CREATE TABLE IF NOT EXISTS _migracao (nome TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS ordem (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL, unit_id INTEGER NOT NULL,
            tipo TEXT NOT NULL, titulo TEXT NOT NULL, descricao TEXT, prioridade TEXT NOT NULL,
            status TEXT NOT NULL, origem TEXT, aberta_em TEXT NOT NULL, concluida_em TEXT, custo REAL,
            responsavel TEXT, historico TEXT, aberta_por INTEGER);
        """
    )
    # Coluna nova (horímetro do serviço), para bases criadas antes dela.
    if "horimetro_h" not in {r[1] for r in c.execute("PRAGMA table_info(servico)")}:
        c.execute("ALTER TABLE servico ADD COLUMN horimetro_h REAL")
    return c


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _grupo_ok(user, group_id: int):
    if getattr(user, "master", 0) or getattr(user, "is_super_admin", False):
        return
    if group_id not in {g for g, _ in (getattr(user, "group_access", None) or [])}:
        raise HTTPException(403, "Sem acesso a este grupo.")


async def _ler(sql: str, p: dict):
    async with AsyncSessionLocalReplica() as db:
        return (await db.execute(text(sql), p)).mappings().all()


async def _frota(group_id: int) -> list[dict]:
    """Veículos ativos do grupo com odômetro atual confiável e últimos sinais do motor."""
    rows = await _ler(
        """
        SELECT tu.id AS unit_id, tu.label AS placa, tu.label2 AS prefixo, tu.model AS modelo, tu.vehicle_year AS ano,
               tu.unit_category_id AS categoria_id, uc.name AS categoria,
               ds.local_time, ds.odom, ds.odom_total, ds.can_total_odometer, ds.odom_quality_flag,
               ds.can_engine_coolant_temp AS temp, ds.can_engine_oil_pressure AS oleo, ds.voltage,
               ds.can_def_level_percent AS arla, ds.can_fuel_level_percent AS combustivel,
               ds.can_pneumatic_system1_pressure AS ar_freio, COALESCE(ds.can_rpm, ds.rpm) AS rpm, ds.ignition,
               ds.can_engine_hourmeter, ds.hourmeter
        FROM mova.tracked_unit tu
        LEFT JOIN mova.unit_category uc ON uc.id = tu.unit_category_id
        LEFT JOIN mova.dev_status ds ON ds.unit_id = tu.id
        WHERE tu.status = 1 AND tu.group_id = :g
        """,
        {"g": group_id},
    )
    out = []
    for r in rows:
        d = dict(r)
        km = odometro_km(d.pop("odom_total"), d.pop("odom"), d.pop("can_total_odometer"))
        qualidade = d.pop("odom_quality_flag")
        d["odometro_km"] = round(km) if km is not None else None
        d["odometro_travado"] = qualidade == "frozen_business_rule"
        h = horimetro_h(d.pop("can_engine_hourmeter"), d.pop("hourmeter"))
        d["horimetro_h"] = round(h) if h else None
        for k in ("temp", "oleo", "voltage", "arla", "combustivel", "ar_freio", "rpm"):
            d[k] = float(d[k]) if d[k] is not None and float(d[k]) != 0 else None
        out.append(d)
    return out


# ------------------------------- Alertas -----------------------------------

# Regras de sinal suspeito (sensor, não motor):
# - óleo com o MESMO valor em todas as leituras com o motor acima de 900 rpm:
#   pressão de óleo de verdade varia com a rotação;
# - ARLA que, no mesmo dia, aparece no fim (≤ 5%) e cheio (≥ 95%): nível de
#   tanque cai devagar e só sobe ao abastecer;
# - ARLA que salta mais de 30 pontos entre uma leitura e a seguinte 3 vezes ou mais
#   em 24 h: abastecer é UM salto para cima; vários saltos são dois sinais misturados
#   (RNY-4F94, 06/10/2026: 90 → 6 → 89 → 0% em 3 minutos, andando a 95 km/h; o
#   alerta "ARLA no fim" saía com a mediana de 8% e o desenho mostrava 74%);
# - tensão abaixo de 5 V: equipamento sem alimentação ou leitura perdida;
# - óleo que cai quando a rotação sobe e nunca passa de ~255: o valor estoura
#   a escala de 1 byte e volta do zero (CECOTI, 05/10/2026: 120–162 kPa entre
#   1.200 e 1.599 rpm e 24–56 kPa acima de 1.600 rpm, com temperatura normal).
#   Na frota toda o efeito começa antes: mediana de 184–188 kPa em marcha
#   lenta (650–899 rpm) e 84–100 kPa entre 900 e 1.499 rpm (CECOTI e Quataí).
#   Por isso o óleo é julgado só em MARCHA LENTA (600–899 rpm), onde a leitura
#   não estoura — e é exatamente onde vale o mínimo Cummins de 69 kPa —, com
#   o motor QUENTE (≥ 75 °C): motor frio tem pressão alta e estoura mesmo em
#   marcha lenta (QOB-6291: 4 a 248 kPa a 69 °C; mediana de 7 dias 188 kPa).
MIN_LEITURAS = 10
ARLA_SALTO_PONTOS = 30
ARLA_SALTOS_MAX = 3
# Vai e vem: subir E descer 10 pontos ou mais, 3 vezes cada, em 24 h = dois sinais misturados mesmo
# quando a distância entre eles é menor que 30 (RUE-5G20, 07/10/2026: lixo 0–2% intercalado com o nível
# real de ~25%; a mediana dava "ARLA no fim 2%"). Só subidas não basta: abastecimento gravado em etapas
# (UAO-0G30: 55 → 88 → 99% em 2 min) e tanque cheio em rampa (PYX-4423: 76–100%) não são defeito.
ARLA_SUBIDA_PONTOS = 10
ARLA_SUBIDAS_MAX = 3
TEMP_JANELA_MIN = 30
TEMP_MIN_LEITURAS = 3
OLEO_TETO = 240  # kPa: perto do limite de 1 byte (255) — a partir daqui a leitura estoura


async def _historico_24h(group_id: int) -> dict[int, dict]:
    rows = await _ler(
        """
        SELECT d.unit_id,
               count(*) FILTER (WHERE d.can_rpm >= 600 AND d.can_engine_oil_pressure > 0) AS oleo_todas,
               count(DISTINCT d.can_engine_oil_pressure) FILTER (WHERE d.can_rpm >= 600 AND d.can_engine_oil_pressure > 0) AS oleo_valores,
               max(d.can_engine_oil_pressure) FILTER (WHERE d.can_rpm >= 600) AS oleo_max,
               count(*) FILTER (WHERE d.can_rpm >= 600 AND d.can_rpm < 900 AND d.can_engine_coolant_temp >= 75 AND d.can_engine_oil_pressure > 0) AS oleo_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.can_engine_oil_pressure)
                   FILTER (WHERE d.can_rpm >= 600 AND d.can_rpm < 900 AND d.can_engine_coolant_temp >= 75 AND d.can_engine_oil_pressure > 0) AS oleo_med,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.can_engine_oil_pressure)
                   FILTER (WHERE d.can_rpm >= 1600 AND d.can_engine_oil_pressure > 0) AS oleo_med_alto,
               count(*) FILTER (WHERE d.can_rpm >= 1600 AND d.can_engine_oil_pressure > 0) AS oleo_n_alto,
               count(*) FILTER (WHERE d.can_rpm >= 500 AND d.voltage >= 5) AS v_lig_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.voltage) FILTER (WHERE d.can_rpm >= 500 AND d.voltage >= 5) AS v_lig_med,
               count(*) FILTER (WHERE coalesce(d.can_rpm, 0) = 0 AND d.voltage >= 5) AS v_desl_n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.voltage) FILTER (WHERE coalesce(d.can_rpm, 0) = 0 AND d.voltage >= 5) AS v_desl_med,
               count(*) FILTER (WHERE d.can_def_level_percent BETWEEN 1 AND 100) AS arla_n,
               count(*) FILTER (WHERE d.can_def_level_percent > 0) AS arla_total,
               count(*) FILTER (WHERE d.can_def_level_percent > 100) AS arla_codigo,
               count(*) FILTER (WHERE d.can_def_level_percent <= 5) AS arla_baixo,
               count(*) FILTER (WHERE d.can_def_level_percent BETWEEN 95 AND 100) AS arla_cheio,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY d.can_def_level_percent) FILTER (WHERE d.can_def_level_percent BETWEEN 1 AND 100) AS arla_med
        FROM mova.dev_status_30 d
        WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
          AND d.local_time >= now() - interval '24 hours'
        GROUP BY d.unit_id
        """,
        {"g": group_id},
    )
    out = {r["unit_id"]: {k: (float(v) if v is not None else None) for k, v in dict(r).items() if k != "unit_id"} for r in rows}
    # Teto da escala do óleo em 7 dias: quem chega a ~255 kPa estoura e volta do
    # zero, e aí leitura baixa não se distingue de alta (RCA: o mesmo caminhão
    # em marcha lenta quente com ~30 kPa num dia e ~200 kPa no outro).
    tetos = await _ler(
        """
        SELECT d.unit_id, max(d.can_engine_oil_pressure) AS oleo_max_7d
        FROM mova.dev_status_30 d
        WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
          AND d.local_time >= now() - interval '7 days' AND d.can_rpm >= 600
        GROUP BY d.unit_id
        """,
        {"g": group_id},
    )
    for r in tetos:
        out.setdefault(r["unit_id"], {})["oleo_max_7d"] = float(r["oleo_max_7d"]) if r["oleo_max_7d"] is not None else None
    saltos = await _ler(
        """
        SELECT x.unit_id, count(*) FILTER (WHERE abs(x.a - x.ant) > :pts) AS arla_saltos,
               count(*) FILTER (WHERE x.a - x.ant >= :sub) AS arla_subidas,
               count(*) FILTER (WHERE x.ant - x.a >= :sub) AS arla_quedas
        FROM (SELECT d.unit_id, d.can_def_level_percent AS a,
                     lag(d.can_def_level_percent) OVER (PARTITION BY d.unit_id ORDER BY d.local_time) AS ant
              FROM mova.dev_status_30 d
              WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
                AND d.local_time >= now() - interval '24 hours'
                AND d.can_def_level_percent BETWEEN 0 AND 100) x
        GROUP BY x.unit_id
        """,
        {"g": group_id, "pts": ARLA_SALTO_PONTOS, "sub": ARLA_SUBIDA_PONTOS},
    )
    for r in saltos:
        out.setdefault(r["unit_id"], {})["arla_saltos"] = float(r["arla_saltos"] or 0)
        out[r["unit_id"]]["arla_subidas"] = float(r["arla_subidas"] or 0)
        out[r["unit_id"]]["arla_quedas"] = float(r["arla_quedas"] or 0)
    # Temperatura dos últimos 30 min com o motor ligado (horário calculado aqui: com now() o banco não
    # descarta as partições diárias).
    temps = await _ler(
        """
        SELECT d.unit_id, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY d.can_engine_coolant_temp) AS med,
               count(*) FILTER (WHERE d.can_engine_coolant_temp >= :crit) AS n_crit
        FROM mova.dev_status_30 d
        WHERE d.unit_id IN (SELECT id FROM mova.tracked_unit WHERE group_id = :g AND status = 1)
          AND d.local_time >= :desde AND d.can_rpm >= 500 AND d.can_engine_coolant_temp BETWEEN 1 AND 150
        GROUP BY d.unit_id
        """,
        {"g": group_id, "desde": datetime.now() - timedelta(minutes=TEMP_JANELA_MIN), "crit": LIMITES["temp_critico"]},
    )
    for r in temps:
        o = out.setdefault(r["unit_id"], {})
        o["temp_n"], o["temp_med"], o["temp_n_crit"] = float(r["n"]), float(r["med"]), float(r["n_crit"])
    return out


def _suspeitos(v: dict, h: dict) -> list[dict]:
    s = []
    if h.get("oleo_todas", 0) >= MIN_LEITURAS and h.get("oleo_valores") == 1:
        s.append({"sinal": "oleo", "titulo": "Pressão de óleo travada",
                  "detalhe": f"O sensor mandou {h['oleo_max']:.0f} kPa em todas as {h['oleo_todas']:.0f} leituras com o motor ligado nas últimas 24 h. Pressão real varia com a rotação: conferir o sensor ou a configuração do CAN."})
    elif (h.get("oleo_max_7d") or 0) >= OLEO_TETO and h.get("oleo_n", 0) >= MIN_LEITURAS and h.get("oleo_med") is not None and h["oleo_med"] < LIMITES["oleo_min_kpa"]:
        s.append({"sinal": "oleo_escala", "titulo": "Pressão de óleo inconclusiva",
                  "detalhe": f"Em marcha lenta a leitura está em {h['oleo_med']:.0f} kPa, mas este sensor chegou a {h['oleo_max_7d']:.0f} kPa nos últimos 7 dias: a escala do equipamento vai só até 255 e volta do zero, então uma leitura baixa pode ser uma pressão alta que estourou. Não dá para afirmar pressão baixa — conferir com manômetro se houver luz de óleo no painel."})
    elif (h.get("oleo_max") or 0) >= 240 and h.get("oleo_n_alto", 0) >= MIN_LEITURAS and h.get("oleo_med") and h.get("oleo_med_alto") is not None             and h["oleo_med_alto"] < h["oleo_med"] / 2:
        s.append({"sinal": "oleo_escala", "titulo": "Pressão de óleo estourando a escala",
                  "detalhe": f"Acima de 1.600 rpm a pressão aparece em {h['oleo_med_alto']:.0f} kPa, menos da metade dos {h['oleo_med']:.0f} kPa em marcha lenta, e nunca passa de {h['oleo_max']:.0f}. A pressão real sobe com a rotação: o valor passa de 255 e o equipamento volta do zero. Para alerta vale só a leitura em marcha lenta."})
    # ARLA acima de 100% = código "sem informação" do J1939 (SPN 1761: 0,4%/bit,
    # 0xFF = 102%). Na CECOTI começou às 12h40–12h44 de 30/09/2026 em 12 VW ao
    # mesmo tempo (mudança no equipamento, não no caminhão).
    if (h.get("arla_codigo") or 0) >= MIN_LEITURAS and h["arla_codigo"] >= (h.get("arla_total") or 0) / 2:
        s.append({"sinal": "arla", "titulo": "ARLA sem leitura válida",
                  "detalhe": f"{h['arla_codigo']:.0f} de {h['arla_total']:.0f} leituras de ARLA nas últimas 24 h vieram como 102% (código de \"sem informação\" do equipamento); os poucos valores entre 0 e 100% aparecem soltos entre eles e não são o nível do tanque. Conferir a configuração do equipamento."})
    elif (h.get("arla_saltos") or 0) >= ARLA_SALTOS_MAX or (
            (h.get("arla_subidas") or 0) >= ARLA_SUBIDAS_MAX and (h.get("arla_quedas") or 0) >= ARLA_SUBIDAS_MAX):
        s.append({"sinal": "arla", "titulo": "Nível de ARLA oscilando",
                  "detalhe": f"Nas últimas 24 h o nível subiu {ARLA_SUBIDA_PONTOS} pontos ou mais de uma leitura para a seguinte {h.get('arla_subidas') or 0:.0f} vezes e desceu {h.get('arla_quedas') or 0:.0f} vezes (saltos de mais de {ARLA_SALTO_PONTOS} pontos: {h.get('arla_saltos') or 0:.0f}). Tanque de ARLA cai devagar e só sobe ao abastecer: o equipamento está misturando dois sinais, e nenhum valor (nem o atual) é confiável. Conferir a configuração do CAN."})
    elif h.get("arla_baixo", 0) > 0 and h.get("arla_cheio", 0) > 0:
        s.append({"sinal": "arla", "titulo": "Nível de ARLA inconsistente",
                  "detalhe": f"Nas últimas 24 h o nível apareceu no fim ({h['arla_baixo']:.0f} leituras com até 5%) e cheio ({h['arla_cheio']:.0f} leituras com 95% ou mais). Conferir o sensor do tanque."})
    tensao = v.get("voltage")
    if tensao is not None and tensao < 5:
        s.append({"sinal": "bateria", "titulo": "Tensão sem leitura",
                  "detalhe": f"Última leitura de {tensao:.1f} V: equipamento sem alimentação ou leitura perdida."
                             + (f" Com o motor ligado, nas últimas 24 h, a tensão ficou em {h['v_lig_med']:.1f} V." if h.get("v_lig_med") else "")})
    return s


def _sinais_bloqueados(suspeitos: list[dict]) -> set[str]:
    """Campos de `sinais` que não podem ser exibidos porque o sensor está suspeito."""
    mapa = {"oleo": "oleo", "oleo_escala": "oleo", "arla": "arla", "bateria": "voltage"}
    return {mapa[x["sinal"]] for x in suspeitos if x["sinal"] in mapa}


def _alertas_do_veiculo(v: dict, h: Optional[dict] = None) -> list[dict]:
    """Sinais fora do normal (corretiva). Cada alerta pode virar OS.

    Temperatura usa a última leitura (superaquecimento é imediato). Óleo,
    tensão e ARLA usam a mediana das últimas 24 h e são ignorados quando o
    sensor está suspeito (ver `_suspeitos`)."""
    a = []
    L = LIMITES
    h = h or {}
    recente = v.get("local_time") and v["local_time"] > datetime.now() - timedelta(hours=6)
    if not recente:
        return a
    suspeitos = {s["sinal"] for s in _suspeitos(v, h)}
    # Temperatura: o que vale é a mediana dos últimos 30 min com o motor ligado; pico isolado sob carga é normal.
    tm, tn = h.get("temp_med"), h.get("temp_n") or 0
    if tm is not None and tn >= TEMP_MIN_LEITURAS:
        if (h.get("temp_n_crit") or 0) >= 2:
            a.append({"chave": "temperatura", "titulo": "Motor superaquecendo", "nivel": "critico", "valor": f"{tm:.0f} °C",
                      "detalhe": f"{h['temp_n_crit']:.0f} leituras com {L['temp_critico']:.0f} °C ou mais nos últimos {TEMP_JANELA_MIN} min com o motor ligado (mediana {tm:.0f} °C; máximo Cummins {L['temp_critico']:.0f} °C)."})
        elif tm >= L["temp_atencao"]:
            a.append({"chave": "temperatura", "titulo": "Motor quente", "nivel": "atencao", "valor": f"{tm:.0f} °C",
                      "detalhe": f"Mediana de {tm:.0f} °C nos últimos {TEMP_JANELA_MIN} min com o motor ligado ({tn:.0f} leituras; atenção a partir de {L['temp_atencao']:.0f} °C sustentados)."})
    # Óleo: mediana em marcha lenta (600–899 rpm) nas últimas 24 h — acima disso a leitura estoura.
    o = h.get("oleo_med")
    if "oleo" not in suspeitos and "oleo_escala" not in suspeitos and o is not None and h.get("oleo_n", 0) >= MIN_LEITURAS and o < L["oleo_min_kpa"]:
        a.append({"chave": "oleo", "titulo": "Pressão de óleo baixa", "nivel": "critico", "valor": f"{o:.0f} kPa",
                  "detalhe": f"Pressão do óleo com mediana de {o:.0f} kPa em {h['oleo_n']:.0f} leituras em marcha lenta com o motor quente nas últimas 24 h (mínimo Cummins em marcha lenta: {L['oleo_min_kpa']:.0f} kPa)."})
    # Tensão: alternador pela mediana com o motor ligado; bateria pela mediana em repouso.
    ref = h.get("v_lig_med") or h.get("v_desl_med") or v.get("voltage")
    if "bateria" not in suspeitos and ref is not None:
        if ref > 18:
            crit, aten, carga, sist = L["v24_critico"], L["v24_atencao"], L["v24_carga_min"], "24 V"
        else:
            crit, aten, carga, sist = L["v12_critico"], L["v12_atencao"], L["v12_carga_min"], "12 V"
        lim_carga, lim_repouso = (L["v24_alerta_carga"], L["v24_alerta_repouso"]) if ref > 18 else (L["v12_alerta_carga"], L["v12_alerta_repouso"])
        vl, vd = h.get("v_lig_med"), h.get("v_desl_med")
        if vl is not None and h.get("v_lig_n", 0) >= MIN_LEITURAS and vl < lim_carga:
            a.append({"chave": "bateria", "titulo": "Alternador sem carregar", "nivel": "critico" if vl < crit else "atencao", "valor": f"{vl:.1f} V",
                      "detalhe": f"Com o motor ligado, a tensão ficou em {vl:.1f} V (mediana de 24 h, medida na alimentação do rastreador) num sistema de {sist}; carregando deveria passar de {carga} V."})
        elif vd is not None and h.get("v_desl_n", 0) >= MIN_LEITURAS and vd < lim_repouso:
            # Bateria fraca não para o veículo (o alternador recarrega): atenção, não crítico.
            a.append({"chave": "bateria", "titulo": "Bateria fraca em repouso", "nivel": "atencao", "valor": f"{vd:.1f} V",
                      "detalhe": f"Com o motor desligado, a tensão ficou em {vd:.1f} V (mediana de 24 h, medida na alimentação do rastreador) num sistema de {sist}; abaixo de {aten} V a bateria tem menos de metade da carga."
                                 + (f" O alternador está carregando ({vl:.1f} V com o motor ligado): o problema é a bateria ou consumo com o veículo parado." if vl and vl >= carga else "")})
    # ARLA: mediana de 24 h das leituras acima de 0 (0 o tempo todo = veículo sem sensor de ARLA).
    arla = h.get("arla_med")
    # Maioria acima de 100% = código de "sem informação" (TDP-2E24: 102% o dia todo e um 5% solto).
    if "arla" not in suspeitos and arla is not None and h.get("arla_n", 0) >= MIN_LEITURAS             and h["arla_n"] >= (h.get("arla_total") or 0) / 2 and arla < L["arla_min"]:
        a.append({"chave": "arla", "titulo": "ARLA 32 no fim", "nivel": "atencao", "valor": f"{arla:.0f}%",
                  "detalhe": f"Nível de ARLA 32 em {arla:.0f}% (mediana de 24 h). Sem ARLA o motor perde potência."})
    # Odômetro travado é qualidade do dado, não defeito do veículo: vai em
    # `odometro_travado` (aviso à parte), não aqui — na Fênix seriam 180 "alertas".
    return a


# ------------------------------- Planos ------------------------------------


class ItemPlano(BaseModel):
    servico: str = Field(..., min_length=2, max_length=80)
    km: Optional[int] = Field(None, ge=100, le=1_000_000)
    dias: Optional[int] = Field(None, ge=1, le=3650)
    horas: Optional[int] = Field(None, ge=10, le=50_000, description="Intervalo em horas de motor (horímetro)")


class Plano(BaseModel):
    group_id: int
    nome: str = Field(..., min_length=2, max_length=80)
    escopo: Literal["categoria", "modelo", "veiculo"]
    alvo: str = Field(..., min_length=1, max_length=120, description="id da categoria, nome do modelo ou id do veículo")
    itens: list[ItemPlano] = Field(..., min_length=1, max_length=40)


@router.get("/modelos-plano")
async def modelos_plano(user=Depends(require_permission("reports", "read"))):
    return MODELOS_PLANO


@router.get("/planos")
async def listar_planos(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        rows = c.execute("SELECT * FROM plano WHERE group_id = ? ORDER BY nome", (group_id,)).fetchall()
    return [{**dict(r), "itens": json.loads(r["itens"])} for r in rows]


@router.post("/planos")
async def salvar_plano(p: Plano, plano_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, p.group_id)
    itens = [i.model_dump() for i in p.itens if i.km or i.dias or i.horas]
    if not itens:
        raise HTTPException(422, "Cada serviço precisa de um intervalo em km, dias ou horas de motor.")
    with _trava, _con() as c:
        if plano_id:
            c.execute("UPDATE plano SET nome=?, escopo=?, alvo=?, itens=?, atualizado_em=?, atualizado_por=? WHERE id=? AND group_id=?",
                      (p.nome, p.escopo, p.alvo, json.dumps(itens, ensure_ascii=False), _agora(), user.user_id, plano_id, p.group_id))
            return {"id": plano_id}
        cur = c.execute("INSERT INTO plano (group_id, nome, escopo, alvo, itens, atualizado_em, atualizado_por) VALUES (?,?,?,?,?,?,?)",
                        (p.group_id, p.nome, p.escopo, p.alvo, json.dumps(itens, ensure_ascii=False), _agora(), user.user_id))
        return {"id": cur.lastrowid}


@router.delete("/planos/{plano_id}", status_code=204)
async def apagar_plano(plano_id: int, group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _trava, _con() as c:
        c.execute("DELETE FROM plano WHERE id = ? AND group_id = ?", (plano_id, group_id))


def _plano_do_veiculo(v: dict, planos: list[dict]) -> Optional[dict]:
    """Veículo > modelo > categoria: o mais específico vale."""
    for escopo, valor in (("veiculo", str(v["unit_id"])), ("modelo", (v.get("modelo") or "").strip().lower()),
                          ("categoria", str(v.get("categoria_id") or ""))):
        for p in planos:
            alvo = p["alvo"].strip().lower() if escopo == "modelo" else p["alvo"]
            if p["escopo"] == escopo and valor and alvo == valor:
                return p
    return None


# ------------------------------- Preventiva --------------------------------


class Servico(BaseModel):
    group_id: int
    unit_id: int
    servico: str = Field(..., min_length=2, max_length=80)
    data: date
    odometro_km: Optional[float] = Field(None, ge=0)
    horimetro_h: Optional[float] = Field(None, ge=0)
    custo: Optional[float] = Field(None, ge=0)
    oficina: Optional[str] = Field(None, max_length=120)
    obs: Optional[str] = Field(None, max_length=1000)
    ordem_id: Optional[int] = None


@router.post("/servicos")
async def registrar_servico(s: Servico, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, s.group_id)
    with _trava, _con() as c:
        cur = c.execute(
            "INSERT INTO servico (group_id, unit_id, servico, data, odometro_km, horimetro_h, custo, oficina, obs, ordem_id, registrado_em, registrado_por)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (s.group_id, s.unit_id, s.servico, s.data.isoformat(), s.odometro_km, s.horimetro_h, s.custo, s.oficina, s.obs, s.ordem_id, _agora(), user.user_id),
        )
        if s.ordem_id:
            _mudar_status(c, s.ordem_id, s.group_id, "concluida", user.user_id, f"Serviço registrado: {s.servico}", custo=s.custo)
    return {"id": cur.lastrowid}


@router.get("/servicos")
async def historico(group_id: int = Query(...), unit_id: Optional[int] = Query(None), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        sql, par = "SELECT * FROM servico WHERE group_id = ?", [group_id]
        if unit_id:
            sql += " AND unit_id = ?"
            par.append(unit_id)
        return [dict(r) for r in c.execute(sql + " ORDER BY data DESC, id DESC LIMIT 500", par).fetchall()]


def _situacao_item(item: dict, ultimo: Optional[dict], km_atual: Optional[float], horas_atual: Optional[float] = None) -> dict:
    hoje = date.today()
    res = {"servico": item["servico"], "intervalo_km": item.get("km"), "intervalo_dias": item.get("dias"), "intervalo_horas": item.get("horas"),
           "ultimo": ultimo and {"data": ultimo["data"], "odometro_km": ultimo["odometro_km"], "horimetro_h": ultimo.get("horimetro_h")}}
    if not ultimo:
        return {**res, "situacao": "sem_registro", "falta_km": None, "falta_dias": None, "falta_horas": None, "proximo_km": None,
                "proxima_data": None, "proximo_horimetro_h": None}
    falta_km = falta_dias = proximo_km = proxima_data = falta_h = proximo_h = None
    if item.get("horas") and ultimo.get("horimetro_h") is not None:
        proximo_h = ultimo["horimetro_h"] + item["horas"]
        if horas_atual is not None:
            falta_h = round(proximo_h - horas_atual)
    if item.get("km") and ultimo["odometro_km"] is not None:
        proximo_km = ultimo["odometro_km"] + item["km"]
        if km_atual is not None:
            falta_km = round(proximo_km - km_atual)
    if item.get("dias"):
        d = date.fromisoformat(ultimo["data"]) + timedelta(days=item["dias"])
        proxima_data = d.isoformat()
        falta_dias = (d - hoje).days
    vencido = (falta_km is not None and falta_km <= 0) or (falta_dias is not None and falta_dias <= 0) or (falta_h is not None and falta_h <= 0)
    breve = (falta_km is not None and falta_km <= AVISO_KM) or (falta_dias is not None and falta_dias <= AVISO_DIAS)         or (falta_h is not None and falta_h <= AVISO_HORAS)
    return {**res, "situacao": "vencido" if vencido else "vence_em_breve" if breve else "em_dia",
            "falta_km": falta_km, "falta_dias": falta_dias, "falta_horas": falta_h, "proximo_km": proximo_km and round(proximo_km),
            "proxima_data": proxima_data, "proximo_horimetro_h": proximo_h and round(proximo_h)}


ORDEM_SIT = {"vencido": 0, "vence_em_breve": 1, "sem_registro": 2, "em_dia": 3}


@router.get("/painel")
async def painel(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Situação de cada veículo: plano, itens vencidos/vencendo, alertas e OS abertas."""
    _grupo_ok(user, group_id)
    frota = await _frota(group_id)
    historico = await _historico_24h(group_id)
    with _con() as c:
        planos = [{**dict(r), "itens": json.loads(r["itens"])} for r in c.execute("SELECT * FROM plano WHERE group_id = ?", (group_id,))]
        servicos = [dict(r) for r in c.execute("SELECT * FROM servico WHERE group_id = ? ORDER BY data DESC, id DESC", (group_id,))]
        ordens = [dict(r) for r in c.execute(
            "SELECT * FROM ordem WHERE group_id = ? AND status NOT IN ('concluida','cancelada')", (group_id,))]
    ultimo: dict[tuple, dict] = {}
    for s in servicos:
        ultimo.setdefault((s["unit_id"], s["servico"].strip().lower()), s)
    os_por_veiculo: dict[int, list] = {}
    for o in ordens:
        os_por_veiculo.setdefault(o["unit_id"], []).append(o)

    veiculos = []
    for v in frota:
        plano = _plano_do_veiculo(v, planos)
        itens = []
        if plano:
            for it in plano["itens"]:
                itens.append(_situacao_item(it, ultimo.get((v["unit_id"], it["servico"].strip().lower())), v["odometro_km"], v.get("horimetro_h")))
            itens.sort(key=lambda i: (ORDEM_SIT[i["situacao"]], i["falta_km"] if i["falta_km"] is not None else 10**9))
        hv = historico.get(v["unit_id"], {})
        alertas = _alertas_do_veiculo(v, hv)
        suspeitos = _suspeitos(v, hv) if v.get("local_time") and v["local_time"] > datetime.now() - timedelta(hours=6) else []
        oss = os_por_veiculo.get(v["unit_id"], [])
        veiculos.append({
            **{k: v[k] for k in ("unit_id", "placa", "prefixo", "modelo", "ano", "categoria_id", "categoria", "odometro_km", "odometro_travado", "horimetro_h")},
            "ultimo_sinal": v["local_time"],
            # Sinal com sensor suspeito não vai para o desenho: mostrar o valor atual dele
            # contradiria o aviso (RNY-4F94: desenho com 74% de ARLA e alerta de 8%).
            "sinais": {k: (None if k in _sinais_bloqueados(suspeitos) else v[k])
                       for k in ("temp", "oleo", "voltage", "arla", "combustivel", "ar_freio", "rpm")},
            "plano": plano and {"id": plano["id"], "nome": plano["nome"], "escopo": plano["escopo"]},
            "itens": itens,
            "vencidos": sum(1 for i in itens if i["situacao"] == "vencido"),
            "vencendo": sum(1 for i in itens if i["situacao"] == "vence_em_breve"),
            "sem_registro": sum(1 for i in itens if i["situacao"] == "sem_registro"),
            "alertas": alertas,
            "sinais_suspeitos": suspeitos,
            "ordens_abertas": len(oss),
            "alertas_com_os": sorted({(o.get("origem") or "").replace("alerta:", "") for o in oss if (o.get("origem") or "").startswith("alerta:")}),
        })
    veiculos.sort(key=lambda x: (-sum(1 for a in x["alertas"] if a["nivel"] == "critico"), -x["vencidos"], -len(x["alertas"]), -x["vencendo"], x["prefixo"] or ""))
    return {
        "totais": {
            "veiculos": len(veiculos),
            "odometro_travado": sum(1 for v in veiculos if v["odometro_travado"]),
            "sinais_suspeitos": sum(len(v["sinais_suspeitos"]) for v in veiculos),
            "com_plano": sum(1 for v in veiculos if v["plano"]),
            "itens_vencidos": sum(v["vencidos"] for v in veiculos),
            "veiculos_com_vencido": sum(1 for v in veiculos if v["vencidos"]),
            "itens_vencendo": sum(v["vencendo"] for v in veiculos),
            "alertas_criticos": sum(1 for v in veiculos for a in v["alertas"] if a["nivel"] == "critico"),
            "alertas": sum(len(v["alertas"]) for v in veiculos),
            "ordens_abertas": len(ordens),
        },
        "limites": LIMITES,
        "veiculos": veiculos,
    }


# ------------------------------- Ordens de serviço -------------------------


class NovaOrdem(BaseModel):
    group_id: int
    unit_id: int
    tipo: Literal["corretiva", "preventiva"] = "corretiva"
    titulo: str = Field(..., min_length=3, max_length=120)
    descricao: Optional[str] = Field(None, max_length=2000)
    prioridade: Literal["baixa", "media", "alta", "critica"] = "media"
    origem: Optional[str] = Field(None, max_length=60, description="alerta:<chave>, plano:<serviço> ou manual")
    responsavel: Optional[str] = Field(None, max_length=120)


class MudancaOrdem(BaseModel):
    group_id: int
    status: Optional[Literal["aberta", "em_andamento", "aguardando_peca", "concluida", "cancelada"]] = None
    responsavel: Optional[str] = Field(None, max_length=120)
    custo: Optional[float] = Field(None, ge=0)
    nota: Optional[str] = Field(None, max_length=1000)


def _mudar_status(c, ordem_id: int, group_id: int, status: Optional[str], por: int, nota: Optional[str] = None, **campos):
    r = c.execute("SELECT * FROM ordem WHERE id = ? AND group_id = ?", (ordem_id, group_id)).fetchone()
    if not r:
        raise HTTPException(404, "Ordem de serviço não encontrada.")
    hist = json.loads(r["historico"] or "[]")
    hist.append({"em": _agora(), "por": por, "status": status or r["status"], "nota": nota})
    sets, vals = ["historico = ?"], [json.dumps(hist, ensure_ascii=False)]
    if status:
        sets.append("status = ?")
        vals.append(status)
        if status in ("concluida", "cancelada"):
            sets.append("concluida_em = ?")
            vals.append(_agora())
    for k, v in campos.items():
        if v is not None:
            sets.append(f"{k} = ?")
            vals.append(v)
    c.execute(f"UPDATE ordem SET {', '.join(sets)} WHERE id = ?", (*vals, ordem_id))


@router.post("/ordens")
async def abrir_ordem(o: NovaOrdem, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, o.group_id)
    with _trava, _con() as c:
        if o.origem and o.origem.startswith("alerta:"):
            ja = c.execute(
                "SELECT id FROM ordem WHERE group_id=? AND unit_id=? AND origem=? AND status NOT IN ('concluida','cancelada')",
                (o.group_id, o.unit_id, o.origem)).fetchone()
            if ja:
                return {"id": ja["id"], "ja_existia": True}
        hist = json.dumps([{"em": _agora(), "por": user.user_id, "status": "aberta", "nota": "Ordem aberta"}], ensure_ascii=False)
        cur = c.execute(
            "INSERT INTO ordem (group_id, unit_id, tipo, titulo, descricao, prioridade, status, origem, aberta_em, responsavel, historico, aberta_por)"
            " VALUES (?,?,?,?,?,?,'aberta',?,?,?,?,?)",
            (o.group_id, o.unit_id, o.tipo, o.titulo, o.descricao, o.prioridade, o.origem or "manual", _agora(), o.responsavel, hist, user.user_id),
        )
        return {"id": cur.lastrowid, "ja_existia": False}


@router.get("/ordens")
async def listar_ordens(group_id: int = Query(...), abertas: bool = Query(False), user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    with _con() as c:
        sql = "SELECT * FROM ordem WHERE group_id = ?"
        if abertas:
            sql += " AND status NOT IN ('concluida','cancelada')"
        rows = c.execute(sql + " ORDER BY aberta_em DESC LIMIT 500", (group_id,)).fetchall()
    return [{**dict(r), "historico": json.loads(r["historico"] or "[]")} for r in rows]


@router.patch("/ordens/{ordem_id}")
async def mudar_ordem(ordem_id: int, m: MudancaOrdem, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, m.group_id)
    with _trava, _con() as c:
        _mudar_status(c, ordem_id, m.group_id, m.status, user.user_id, m.nota, responsavel=m.responsavel, custo=m.custo)
    return {"ok": True}
