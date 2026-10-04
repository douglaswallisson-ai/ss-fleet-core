"""
Cadastros da plataforma (empresa, unidades/subgrupos, garagens, veículos,
dispositivos, vínculos, usuários, alarmes, cercas, pontos de interesse,
pontos de parada e linhas).

As regras são as do sistema atual (plataforma_web), lidas no vault em
04/10/2026 (Telas/Unidades, Grupos, Dispositivos, Usuarios, Alarmes, Cerca,
POI). Onde o vault registra erro do sistema atual, aqui está corrigido e
anotado (ex.: Média de Consumo que nunca era gravada; IMEI com hífen recusado).

Leitura: banco (só leitura). Gravação: armazenamento provisório
`data/cadastros.sqlite` — o que se cria, edita ou exclui aqui aparece junto
com o dado real, marcado como provisório, e não chega ao sistema atual nem aos
equipamentos até a gravação ser liberada (decisão do PM, 03/10/2026).
"""

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import AsyncSessionLocalReplica
from app.middleware.auth import require_permission

router = APIRouter()

ARQUIVO = Path(__file__).resolve().parents[4] / "data" / "cadastros.sqlite"
_trava = threading.Lock()
_CACHE: dict[tuple, tuple[float, Any]] = {}
CACHE_S = 120

OPERADORAS = ["Algar - Multioperadora", "ALLCON", "CLARO", "Claro - 4G", "Claro - M2M", "Guardian", "OI", "Skywave", "TIM",
              "Transmeet - Claro", "Transmeet - Multioperadora", "Transmeet - Vivo", "VIVO", "Vivo - 4G", "Vivo - 4G - CITTATI", "WIFI"]
NIVEIS_ALARME = {1: "Baixo", 2: "Médio", 3: "Alto"}


# ------------------------------------------------------------- armazenamento

def _con() -> sqlite3.Connection:
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(ARQUIVO)
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS registro (tipo TEXT NOT NULL, id TEXT NOT NULL, group_id INTEGER NOT NULL,
            origem_id INTEGER, dados TEXT NOT NULL, excluido INTEGER DEFAULT 0, motivo TEXT, autor INTEGER, em TEXT,
            PRIMARY KEY (tipo, id));
        CREATE TABLE IF NOT EXISTS historico (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, registro_id TEXT,
            acao TEXT, dados TEXT, motivo TEXT, autor INTEGER, em TEXT);
        """
    )
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
        return [dict(r) for r in (await db.execute(text(sql), p)).mappings().all()]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _so_digitos(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


class Erro(Exception):
    def __init__(self, campo: Optional[str], msg: str):
        self.campo, self.msg = campo, msg


def _exigir(d: dict, *campos: tuple[str, str]):
    for c, rot in campos:
        if d.get(c) in (None, "", []):
            raise Erro(c, f"{rot} é obrigatório.")


# ------------------------------------------------------------- definições

class Tipo:
    def __init__(self, nome: str, sql: Optional[str], validar: Callable, unicos: Callable = lambda d: [],
                 pode_excluir: Optional[Callable] = None, exige_motivo: bool = False, so_ss_cria: bool = False):
        self.nome, self.sql, self.validar, self.unicos = nome, sql, validar, unicos
        self.pode_excluir, self.exige_motivo, self.so_ss_cria = pode_excluir, exige_motivo, so_ss_cria


# Empresa (grupo) — vault Telas/Grupos.
SQL_EMPRESA = """SELECT g.id, g.name AS nome, g.corporate_name AS razao_social, g.address AS endereco, g.contact AS contatos,
    g.cnpj, g.max_speed AS velocidade_max, g.pro_rata, g.client_cod AS codigo_cliente,
    (SELECT count(*) FROM mova.subgroup s WHERE s.group_id = g.id) AS subgrupos,
    (SELECT count(*) FROM mova.tracked_unit t WHERE t.group_id = g.id AND t.status = 1) AS veiculos
    FROM mova."group" g WHERE g.id = :g"""


def _v_empresa(d, ctx):
    _exigir(d, ("nome", "Nome do grupo"), ("velocidade_max", "Velocidade máxima"))
    logo = d.get("logo")
    if logo:
        if not str(logo).startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/svg+xml;base64,", "data:image/webp;base64,")):
            raise Erro("logo", "A logo deve ser uma imagem PNG, JPG, SVG ou WEBP.")
        if len(logo) > 700_000:
            raise Erro("logo", "A logo deve ter no máximo 500 KB.")
    if d.get("cnpj") and len(_so_digitos(d["cnpj"])) != 14:
        raise Erro("cnpj", "CNPJ deve ter 14 dígitos (99.999.999/9999-99).")
    if not 0 <= (_num(d["velocidade_max"]) or 0) <= 200:
        raise Erro("velocidade_max", "Velocidade máxima entre 0 e 200 km/h.")


# Unidades (subgrupos / filiais) — vault Telas/Grupos, seção Subgrupos.
SQL_SUBGRUPO = """SELECT s.id, s.name AS nome, s.company AS empresa, s.address AS endereco, s.cnpj, s.color AS cor,
    s.tolerance_before_ini AS tolerancia_antes, s.tolerance_after_ini AS tolerancia_depois, s.client_cod AS codigo_cliente,
    s.suspended AS suspenso, (SELECT count(*) FROM mova.tracked_unit t WHERE t.subgroup_id = s.id AND t.status = 1) AS veiculos
    FROM mova.subgroup s WHERE s.group_id = :g"""


def _v_subgrupo(d, ctx):
    _exigir(d, ("nome", "Nome"))
    d.setdefault("cor", "#000000")
    for k in ("tolerancia_antes", "tolerancia_depois"):
        if d.get(k) in (None, ""):
            d[k] = 5  # padrão do sistema atual
    if d.get("cnpj") and len(_so_digitos(d["cnpj"])) != 14:
        raise Erro("cnpj", "CNPJ deve ter 14 dígitos.")


async def _excluir_subgrupo(row, ctx):
    if not row.get("origem_id"):
        return None
    r = await _ler("""SELECT (SELECT count(*) FROM mova.tracked_unit WHERE subgroup_id = :s AND status = 1) AS u,
                             (SELECT count(*) FROM mova.driver WHERE subgroup_id = :s AND status = 1) AS d,
                             (SELECT count(*) FROM mova.alarm WHERE subgroup_id = :s AND status = 1) AS a""", {"s": row["origem_id"]})
    r = r[0]
    partes = [f"{r['u']} veículo(s)" if r["u"] else "", f"{r['d']} motorista(s)" if r["d"] else "", f"{r['a']} alarme(s)" if r["a"] else ""]
    partes = [x for x in partes if x]
    return f"Há {', '.join(partes)} nesta unidade. Mova-os antes de excluir." if partes else None


# Garagens: não existem no banco; ficam só na plataforma.
def _v_garagem(d, ctx):
    _exigir(d, ("nome", "Nome"), ("subgroup_id", "Unidade"))
    if d.get("latitude") not in (None, "") and not -90 <= _num(d["latitude"]) <= 90:
        raise Erro("latitude", "Latitude inválida.")


# Veículos — vault Telas/Unidades.
SQL_VEICULO = """SELECT tu.id, tu.label AS placa, tu.label2 AS descricao, tu.label_cart AS carreta, tu.unit_type_id AS tipo_id,
    tu.unit_category_id AS categoria_id, uc.name AS categoria, tu.subgroup_id, sg.name AS subgrupo, tu.max_speed AS velocidade_max,
    tu.qtd_passenger AS passageiros, tu.driver_id AS condutor_id, d.name AS condutor, tu.timezone AS fuso, tu.dst AS horario_verao,
    tu.mid_fuel AS media_consumo, tu.obs AS observacoes, (tu.initial_odometer / 1000.0) AS odometro_inicial_km,
    tu.initial_horimeter AS horimetro_inicial, tu.model AS modelo, tu.vehicle_year AS ano, tu.status
    FROM mova.tracked_unit tu LEFT JOIN mova.unit_category uc ON uc.id = tu.unit_category_id
    LEFT JOIN mova.subgroup sg ON sg.id = tu.subgroup_id LEFT JOIN mova.driver d ON d.id = tu.driver_id
    WHERE tu.group_id = :g AND tu.status IN (1, 2)"""
PLACA = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9][0-9]{2}$")


def _v_veiculo(d, ctx):
    _exigir(d, ("placa", "Placa"), ("subgroup_id", "Unidade (subgrupo)"))
    p = re.sub(r"[^A-Z0-9]", "", str(d["placa"]).upper())
    if not PLACA.match(p):
        raise Erro("placa", "Formato da placa incorreto (ex.: ABC-1234 ou ABC1D23).")
    d["placa"] = f"{p[:3]}-{p[3:]}"
    d["velocidade_max"] = int(_num(d.get("velocidade_max")) or 90)  # padrão 90 do sistema atual
    d["passageiros"] = int(_num(d.get("passageiros")) or 0)
    d["fuso"] = int(_num(d.get("fuso")) if d.get("fuso") not in (None, "") else -3)
    if d.get("odometro_inicial_km") not in (None, "") and (_num(d["odometro_inicial_km"]) or 0) < 0:
        raise Erro("odometro_inicial_km", "Odômetro inicial não pode ser negativo.")
    if d.get("media_consumo") not in (None, "") and not 0 < (_num(str(d["media_consumo"]).replace(",", ".")) or 0) < 30:
        raise Erro("media_consumo", "Média de consumo entre 0 e 30 km/L.")
    if d.get("ano") not in (None, "") and not 1980 <= int(_num(d["ano"]) or 0) <= datetime.now().year + 1:
        raise Erro("ano", "Ano inválido.")


def _u_veiculo(d):
    return [("placa", "Já existe um veículo com esta placa nesta empresa.", lambda r: re.sub(r"[^A-Z0-9]", "", str(r.get("placa") or "").upper()))]


# Dispositivos — vault Telas/Dispositivos.
SQL_DISPOSITIVO = """SELECT dv.id, dv.identifier AS identificador, dv.device_model_id AS modelo_id, dm.name AS modelo,
    dm.device_manufacturer_id AS fabricante_id, mf.name AS fabricante, dv.imei, dv.iccid, dv.serial_number AS serial,
    dv.operadora, dv.number AS linha, dv.device_type AS tipo, dv.fw_version AS firmware, dv.current_product_id AS produto_id,
    (SELECT tu.label FROM mova.tracked_unit_device tud JOIN mova.tracked_unit tu ON tu.id = tud.tracked_unit_id
       WHERE tud.device_id = dv.id AND tud.status = 1 LIMIT 1) AS placa
    FROM mova.device dv LEFT JOIN mova.device_model dm ON dm.id = dv.device_model_id
    LEFT JOIN mova.device_manufacturer mf ON mf.id = dm.device_manufacturer_id
    WHERE dv.status = 1 AND (dv.group_id = :g OR dv.id IN (SELECT tud.device_id FROM mova.tracked_unit_device tud
       JOIN mova.tracked_unit tu ON tu.id = tud.tracked_unit_id WHERE tu.group_id = :g AND tud.status = 1))"""


def _luhn(s: str) -> bool:
    soma = 0
    for i, ch in enumerate(reversed(s)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        soma += n
    return soma % 10 == 0


def _v_dispositivo(d, ctx):
    _exigir(d, ("identificador", "Identificador"), ("modelo_id", "Modelo"), ("produto_id", "Produto"))
    if not re.fullmatch(r"[A-Za-z0-9]{1,50}", str(d["identificador"])):
        raise Erro("identificador", "Identificador: apenas letras e números, até 50 caracteres.")
    if d.get("imei"):
        # Correção do sistema atual: lá o IMEI ia com hífens e era recusado por tamanho.
        imei = _so_digitos(d["imei"])
        if len(imei) != 15:
            raise Erro("imei", "IMEI deve ter 15 dígitos.")
        d["imei"] = imei
        if not _luhn(imei):
            ctx["avisos"].append("O IMEI não passa no dígito verificador (Luhn). Confira se não é IMEI de teste.")
    if d.get("iccid"):
        if not re.fullmatch(r"\d{1,22}", _so_digitos(d["iccid"])):
            raise Erro("iccid", "ICCID: até 22 dígitos.")
        d["iccid"] = _so_digitos(d["iccid"])
    if d.get("linha"):
        n = _so_digitos(d["linha"])
        if len(n) not in (10, 11):
            raise Erro("linha", "Número da linha no formato (99) 99999-9999.")
        d["linha"] = f"({n[:2]}) {n[2:-4]}-{n[-4:]}"
    if d.get("operadora") and d["operadora"] not in OPERADORAS:
        raise Erro("operadora", "Operadora inválida.")
    if d.get("tipo") not in (None, "", "ANALOGICO", "CAN"):
        raise Erro("tipo", "Tipo deve ser ANALOGICO ou CAN.")


def _u_dispositivo(d):
    return [("identificador", "O identificador já está cadastrado para este modelo.",
             lambda r: f"{r.get('modelo_id')}:{str(r.get('identificador') or '').upper()}"),
            ("imei", "Já existe um dispositivo ativo com este IMEI.", lambda r: _so_digitos(r.get("imei")) or None),
            ("linha", "Já existe um dispositivo ativo com este número de linha.", lambda r: _so_digitos(r.get("linha")) or None)]


async def _excluir_dispositivo(row, ctx):
    if row.get("placa"):
        return f"O dispositivo está no veículo {row['placa']}. Desvincule em Equipamentos por veículo antes de excluir."
    return None


# Vínculo veículo ↔ dispositivo (Equipamentos por veículo).
SQL_VINCULO = """SELECT tud.id, tud.tracked_unit_id AS unit_id, tu.label AS placa, tu.label2 AS descricao, tud.device_id,
    dv.identifier AS identificador, dm.name AS modelo, tud.device_primary AS primario, tud.association_date AS desde
    FROM mova.tracked_unit_device tud JOIN mova.tracked_unit tu ON tu.id = tud.tracked_unit_id
    JOIN mova.device dv ON dv.id = tud.device_id LEFT JOIN mova.device_model dm ON dm.id = dv.device_model_id
    WHERE tu.group_id = :g AND tud.status = 1 AND tu.status = 1"""


def _v_vinculo(d, ctx):
    _exigir(d, ("unit_id", "Veículo"), ("device_id", "Dispositivo"))


def _u_vinculo(d):
    return [("device_id", "Este dispositivo já está vinculado a outro veículo.", lambda r: r.get("device_id"))]


# Usuários — vault Telas/Usuarios. Senha não é tratada aqui (o sistema atual gera e manda por e-mail).
SQL_USUARIO = """SELECT u.id, u.name AS nome, u.login, u.email,
    CASE WHEN u.master = 1 THEN 'admin_empresa' ELSE 'gestor' END AS perfil, u.user_web AS web, u.user_mobile AS mobile, u.user_mova AS usuario_ss,
    u.master AS administrador, u.hour_start AS hora_inicio, u.hour_end AS hora_fim, u.day_start AS dia_inicio, u.day_end AS dia_fim,
    u.end_access AS acesso_ate,
    (SELECT array_agg(DISTINCT uga.subgroup_id) FROM mova.user_group_access uga WHERE uga.user_id = u.id AND uga.group_id = :g) AS subgrupos
    FROM mova.users u WHERE u.status = 1 AND u.id IN (SELECT user_id FROM mova.user_group_access WHERE group_id = :g)"""
EMAIL = re.compile(r"^[^@\s;]+@[^@\s;]+\.[^@\s;]+$")


def _v_usuario(d, ctx):
    _exigir(d, ("nome", "Nome"), ("login", "Login"), ("email", "E-mail"), ("perfil", "Perfil de acesso"))
    if not EMAIL.match(str(d["email"]).strip()):
        raise Erro("email", "E-mail inválido.")
    if not d.get("subgrupos"):
        raise Erro("subgrupos", "Dê acesso a pelo menos uma unidade (subgrupo).")
    if d.get("usuario_ss") and not ctx["ss"]:
        raise Erro("usuario_ss", "Somente usuários SS podem cadastrar usuários SS.")
    if d.get("hora_inicio") and d.get("hora_fim") and d["hora_fim"] <= d["hora_inicio"]:
        raise Erro("hora_fim", "O horário final deve ser depois do inicial.")


def _u_usuario(d):
    return [("login", "Este login já existe cadastrado em nosso sistema.", lambda r: str(r.get("login") or "").strip().lower() or None)]


# Alarmes — vault Telas/Alarmes.
SQL_ALARME = """SELECT a.id, a.name AS nome, a.subgroup_id, a.level AS nivel, a.description AS descricao,
    a.notif_monitor AS notificar_monitor, a.notif_email AS notificar_email, a.email_notification AS emails, a.pair_alarm_id,
    (SELECT json_agg(json_build_object('parametro_id', c.parameter_id, 'operador', c.operator, 'valor', c.value,
            'cam_interval', c.cam_interval, 'cam_qtd', c.cam_qtd, 'km_tolerance', c.km_tolerance) ORDER BY c.id)
       FROM mova.alarm_config c WHERE c.alarm_id = a.id) AS regras,
    (SELECT array_agg(atu.unit_id) FROM mova.alarm_tracked_unit atu WHERE atu.alarm_id = a.id AND atu.status = 1) AS veiculos
    FROM mova.alarm a WHERE a.group_id = :g AND a.status = 1"""


def _v_alarme(d, ctx):
    _exigir(d, ("nome", "Nome do alarme"))
    regras = d.get("regras") or []
    if not regras:
        raise Erro("regras", "Inclua pelo menos uma regra (\"Disparar alarme quando…\").")
    d["nivel"] = int(_num(d.get("nivel")) or 1)
    if d["nivel"] not in NIVEIS_ALARME:
        raise Erro("nivel", "Nível crítico: Baixo, Médio ou Alto.")
    if len(d.get("descricao") or "") > 250:
        raise Erro("descricao", "Descrição: até 250 caracteres.")
    if sum(1 for r in regras if int(_num(r.get("parametro_id")) or 0) == 15) > 1:
        raise Erro("regras", "Só pode haver um item do tipo Evento.")
    if sum(1 for r in regras if int(_num(r.get("parametro_id")) or 0) == 25) > 1:
        raise Erro("regras", "Só pode haver um item do tipo Evento de câmera.")
    for r in regras:
        pid = int(_num(r.get("parametro_id")) or 0)
        if not pid or r.get("valor") in (None, ""):
            raise Erro("regras", "Toda regra precisa de parâmetro e valor.")
        if pid == 26 and not r.get("km_sem_ocorrencia"):
            raise Erro("regras", "Cessação de evento: informe a distância sem ocorrência (km).")
        if pid == 28 and not r.get("kml_maximo"):
            raise Erro("regras", "Consumo (limite fixo): informe o km/L máximo.")
        if pid == 30 and not (r.get("janela_min") and r.get("sustentacao_s") and r.get("tolerancia_km")):
            raise Erro("regras", "Queda de combustível: informe janela, sustentação e tolerância.")
    emails = (d.get("emails") or "").replace(" ", "").replace('"', "").replace("'", "")
    if len(emails) > 500:
        raise Erro("emails", "E-mails: até 500 caracteres.")
    for e in [x for x in emails.split(";") if x]:
        if not EMAIL.match(e):
            raise Erro("emails", f"E-mail inválido: {e}. Separe os e-mails com \";\".")
    d["emails"] = emails
    if d.get("notificar_email") and not emails:
        raise Erro("emails", "Informe os e-mails ou desmarque \"Enviar notificação por e-mail\".")


# Cercas — vault Telas/Cerca.
SQL_CERCA = """SELECT c.id, c.name AS nome, c.subgroup_id, c.polygroup_type AS tipo, c.color AS cor, c.speed AS velocidade,
    c.description AS descricao, c.radius AS raio_m, c.latitude, c.longitude, c.vector_weight AS espessura,
    c.cerca_category_account_id AS categoria_id, cc.nome AS categoria, c.memory IS NOT NULL AND c.memory > 0 AS embarcada,
    CASE WHEN c.polygon IS NOT NULL THEN ST_AsGeoJSON(c.polygon)::text END AS geometria
    FROM mova.cerca c LEFT JOIN mova.cerca_category_account cca ON cca.id = c.cerca_category_account_id
    LEFT JOIN mova.cerca_category cc ON cc.id = cca.category_id
    WHERE c.group_id = :g AND c.status = 1"""
NOME_CERCA = re.compile(r"^[A-Za-z0-9 \-_@/!#$%^&*()+{}\[\]:;<>,.?~`\\]+$")


def _v_cerca(d, ctx):
    _exigir(d, ("nome", "Nome da cerca"), ("categoria_id", "Categoria"), ("tipo", "Tipo (desenho)"))
    if not NOME_CERCA.match(str(d["nome"])):
        raise Erro("nome", "Nome da cerca inválido! Caracteres especiais (inclusive acentos) não são permitidos.")
    tipo = int(_num(d["tipo"]) or 0)
    if tipo == 1:
        _exigir(d, ("latitude", "Centro"), ("raio_m", "Raio"))
        if d.get("embarcada"):
            raise Erro("embarcada", "Não é possível embarcar cercas circulares.")
    else:
        pts = d.get("pontos") or []
        if len(pts) < (2 if tipo == 4 else 3):
            raise Erro("pontos", "É preciso desenhar uma cerca.")
        maximo = 32 if tipo == 4 else (50 if ctx.get("account_id") == 539 else 31)
        if len(pts) > maximo:
            raise Erro("pontos", f"Sua cerca está com {len(pts)} pontos, e o máximo suportado pelo sistema é de {maximo} pontos.")
        if tipo == 4 and int(_num(d.get("espessura")) or 0) not in (15, 25, 50):
            raise Erro("espessura", "Espessura do vetor: 15, 25 ou 50 metros.")
    if not 0 <= int(_num(d.get("velocidade")) or 0) <= 300:
        raise Erro("velocidade", "Velocidade entre 0 e 300 km/h.")
    if len(d.get("descricao") or "") > 250:
        raise Erro("descricao", "Descrição: até 250 caracteres.")
    d.setdefault("cor", "#808080")


# Pontos de interesse — vault Telas/POI.
SQL_POI = """SELECT p.id, p.name AS nome, p.latitude, p.longitude, p.radius AS raio_m, p.icon AS icone, p.color AS cor,
    p.description AS descricao, p.poi_category_account_id AS categoria_id, pc.nome AS categoria, p.memory IS NOT NULL AND p.memory > 0 AS embarcado
    FROM mova.poi p LEFT JOIN mova.poi_category_account pca ON pca.id = p.poi_category_account_id
    LEFT JOIN mova.poi_category pc ON pc.id = pca.category_id
    WHERE p.group_id = :g AND p.status = 1"""


def _v_poi(d, ctx):
    _exigir(d, ("nome", "Nome do local"), ("latitude", "Latitude"), ("longitude", "Longitude"), ("categoria_id", "Categoria"))
    if re.search(r"['\"|]", str(d["nome"])):
        raise Erro("nome", "Nome do local inválido! Aspas duplas, simples e | não são permitidas.")
    if not -90 <= (_num(d["latitude"]) or 999) <= 90 or not -180 <= (_num(d["longitude"]) or 999) <= 180:
        raise Erro("latitude", "Coordenada inválida.")
    raio = int(_num(d.get("raio_m")) or 50)  # padrão 50 do sistema atual
    if not 10 <= raio <= 5000:
        raise Erro("raio_m", "Raio entre 10 e 5.000 metros.")
    d["raio_m"] = raio
    d.setdefault("cor", "#0B03FD")
    if len(d.get("descricao") or "") > 250:
        raise Erro("descricao", "Descrição: até 250 caracteres.")


# Pontos de parada / pontos de controle das linhas.
SQL_PONTO_PARADA = """SELECT p.id, p.name AS nome, p.latitude, p.longitude, p.radius AS raio_m,
    (SELECT array_agg(DISTINCT bl.name) FROM mova.buss_line_poi blp JOIN mova.buss_line bl ON bl.id = blp.buss_line_id
       WHERE blp.poi_id = p.id AND blp.status = 1) AS linhas
    FROM mova.poi p WHERE p.status = 1 AND p.id IN (
       SELECT blp.poi_id FROM mova.buss_line_poi blp JOIN mova.buss_line bl ON bl.id = blp.buss_line_id
       WHERE bl.group_id = :g AND blp.status = 1 AND bl.status = 1
       UNION SELECT x.poi_id FROM mova.buss_line_shift_poi x JOIN mova.buss_line_shift s ON s.id = x.buss_line_shift_id
       JOIN mova.buss_line bl ON bl.id = s.buss_line_id WHERE bl.group_id = :g AND bl.status = 1)"""


def _v_ponto_parada(d, ctx):
    _exigir(d, ("nome", "Nome"), ("latitude", "Latitude"), ("longitude", "Longitude"))
    d["raio_m"] = int(_num(d.get("raio_m")) or 50)
    d.setdefault("papel", "parada")
    if d["papel"] not in ("parada", "controle"):
        raise Erro("papel", "Papel: ponto de parada ou ponto de controle.")


# Linhas — buss_line.
SQL_LINHA = """SELECT bl.id, bl.name AS nome, bl.description AS descricao, bl.subgroup_id, sg.name AS subgrupo, bl.km,
    bl.duration AS duracao_min, bl.circular, bl.type_id AS modalidade_id,
    (SELECT array_agg(blp.poi_id ORDER BY blp.id) FROM mova.buss_line_poi blp WHERE blp.buss_line_id = bl.id AND blp.status = 1) AS pontos
    FROM mova.buss_line bl LEFT JOIN mova.subgroup sg ON sg.id = bl.subgroup_id WHERE bl.group_id = :g AND bl.status = 1"""


def _v_linha(d, ctx):
    _exigir(d, ("nome", "Nome da linha"))
    if d.get("km") not in (None, "") and not 0 < (_num(d["km"]) or 0) < 2000:
        raise Erro("km", "Extensão entre 0 e 2.000 km.")
    if d.get("duracao_min") not in (None, "") and not 0 < (_num(d["duracao_min"]) or 0) < 1440:
        raise Erro("duracao_min", "Duração em minutos, menor que 24 h.")


def _u_nome(msg):
    return lambda d: [("nome", msg, lambda r: str(r.get("nome") or "").strip().lower() or None)]


TIPOS: dict[str, Tipo] = {
    "empresa": Tipo("Empresa", SQL_EMPRESA, _v_empresa, so_ss_cria=True),
    "subgrupo": Tipo("Unidade", SQL_SUBGRUPO, _v_subgrupo, _u_nome("Já existe uma unidade com este nome."), pode_excluir=_excluir_subgrupo),
    "garagem": Tipo("Garagem", None, _v_garagem, _u_nome("Já existe uma garagem com este nome.")),
    "veiculo": Tipo("Veículo", SQL_VEICULO, _v_veiculo, _u_veiculo, exige_motivo=True),
    "dispositivo": Tipo("Dispositivo", SQL_DISPOSITIVO, _v_dispositivo, _u_dispositivo, pode_excluir=_excluir_dispositivo),
    "vinculo": Tipo("Vínculo de equipamento", SQL_VINCULO, _v_vinculo, _u_vinculo),
    "usuario": Tipo("Usuário", SQL_USUARIO, _v_usuario, _u_usuario),
    "alarme": Tipo("Alarme", SQL_ALARME, _v_alarme, _u_nome("Já existe um alarme com este nome.")),
    "cerca": Tipo("Cerca", SQL_CERCA, _v_cerca, _u_nome("Já existe uma cerca com este nome.")),
    "poi": Tipo("Ponto de interesse", SQL_POI, _v_poi),
    "ponto_parada": Tipo("Ponto de parada", SQL_PONTO_PARADA, _v_ponto_parada),
    "linha": Tipo("Linha", SQL_LINHA, _v_linha, _u_nome("Já existe uma linha com este nome.")),
}


# ------------------------------------------------------------- fretamento
# Regras do sistema atual (plataforma_web: passengercontroller, routecontroller,
# costcentercontroller, linegroupcontroller, buslineshiftshiftcontroller,
# seat_layoutcontroller), lidas em 04/10/2026.

SQL_PASSAGEIRO = """SELECT p.id, p.name AS nome, p.cpf, p.matricula, p.cod AS cartao, p.company AS empresa,
    p.management AS gerencia, p.general_management AS gerencia_geral, p.cost_center_id, cc.name AS centro_custo,
    p.subgroup_id, p.shift_id AS turno_id, p.seat_number AS assento, p.hour_ini AS hora_inicio, p.hour_end AS hora_fim,
    p.rua, p.numero, p.bairro, p.cidade, p.cep, p.observation AS observacao, p.inactivity AS inativo,
    (SELECT count(*) FROM mova.buss_line_shift_passenger blsp WHERE blsp.passenger_id = p.id AND blsp.status = 1) AS viagens,
    pi.date_ini AS inatividade_inicio, pi.date_end AS inatividade_fim
    FROM mova.passenger p LEFT JOIN mova.cost_center cc ON cc.id = p.cost_center_id
    LEFT JOIN LATERAL (SELECT date_ini, date_end FROM mova.passenger_inactivity i WHERE i.passenger_id = p.id AND i.status = 1
                       ORDER BY i.date_ini DESC LIMIT 1) pi ON true
    WHERE p.group_id = :g AND p.status = 1"""


def _v_passageiro(d, ctx):
    _exigir(d, ("nome", "Nome"), ("cost_center_id", "Centro de custo"))
    if d.get("cpf"):
        cpf = _so_digitos(d["cpf"])
        if len(cpf) != 11:
            raise Erro("cpf", "CPF deve ter 11 dígitos.")
        d["cpf"] = cpf
    if d.get("cep") and len(_so_digitos(d["cep"])) != 8:
        raise Erro("cep", "CEP deve ter 8 dígitos.")
    if d.get("hora_inicio") and d.get("hora_fim") and str(d["hora_fim"]) <= str(d["hora_inicio"]):
        raise Erro("hora_fim", "O horário final deve ser depois do inicial.")
    if d.get("inatividade_inicio") and d.get("inatividade_fim") and str(d["inatividade_fim"]) < str(d["inatividade_inicio"]):
        raise Erro("inatividade_fim", "O fim da inatividade deve ser depois do início.")


def _u_passageiro(d):
    return [("cpf", "Já existe um passageiro com este CPF.", lambda r: _so_digitos(r.get("cpf")) or None),
            ("cartao", "Este código de cartão (RFID) já está com outro passageiro.", lambda r: str(r.get("cartao") or "").strip() or None)]


SQL_CENTRO_CUSTO = """SELECT cc.id, cc.name AS nome, cc.integration_id AS codigo_integracao,
    (SELECT count(*) FROM mova.passenger p WHERE p.cost_center_id = cc.id AND p.status = 1) AS passageiros,
    (SELECT count(*) FROM mova.buss_line bl WHERE bl.cost_center_id = cc.id AND bl.status = 1) AS linhas
    FROM mova.cost_center cc WHERE cc.group_id = :g AND cc.status = 1"""


def _v_centro_custo(d, ctx):
    _exigir(d, ("nome", "Nome do centro de custo"))


async def _excluir_centro_custo(row, ctx):
    if int(row.get("passageiros") or 0) or int(row.get("linhas") or 0):
        return f"Há {row.get('passageiros') or 0} passageiro(s) e {row.get('linhas') or 0} linha(s) neste centro de custo. Mova-os antes de excluir."
    return None


SQL_TURNO = """SELECT s.id, s.name AS nome,
    (SELECT count(*) FROM mova.passenger p WHERE p.shift_id = s.id AND p.status = 1) AS passageiros
    FROM mova.bus_line_shift_shift s WHERE s.group_id = :g AND s.status = 1"""


def _v_turno(d, ctx):
    _exigir(d, ("nome", "Nome do turno"))


SQL_GRUPO_LINHAS = """SELECT lg.id, lg.name AS nome, lg.lines AS linhas, lg.subgroup_id
    FROM mova.line_group lg WHERE lg.group_id = :g AND lg.status = 1"""


def _v_grupo_linhas(d, ctx):
    _exigir(d, ("nome", "Nome do grupo de linhas"))
    if not d.get("linhas"):
        raise Erro("linhas", "Escolha pelo menos uma linha.")


SQL_ROTA = """SELECT r.id, r.name AS nome, r.color AS cor, r.description AS descricao, r.speed AS velocidade,
    r.cost_center_id, cc.name AS centro_custo, r.total_point AS pontos_total,
    round((ST_Length(g.geo::geography) / 1000)::numeric, 1) AS km,
    ST_AsGeoJSON(ST_Simplify(g.geo, 0.0002))::text AS trajeto_geojson
    FROM mova.route r LEFT JOIN mova.cost_center cc ON cc.id = r.cost_center_id
    -- O sistema atual grava a rota com X = latitude (igual às cercas). No Brasil a
    -- latitude nunca fica abaixo de -34, então X < -34 indica que já está em lng/lat.
    CROSS JOIN LATERAL (SELECT CASE WHEN ST_X(ST_StartPoint(r.route)) < -34 THEN r.route
                                    ELSE ST_FlipCoordinates(r.route) END AS geo) g
    WHERE r.group_id = :g AND r.status = 1"""


def _v_rota(d, ctx):
    _exigir(d, ("nome", "Nome da rota"))
    d.setdefault("cor", "#0000FF")
    if not 0 <= int(_num(d.get("velocidade")) or 0) <= 300:
        raise Erro("velocidade", "Velocidade entre 0 e 300 km/h.")


SQL_LAYOUT = """SELECT l.id, l.name AS nome, l.qtd AS assentos, l.description AS descricao, l.cost_center_id, l.layout AS imagem
    FROM mova.bls_seat_layout l WHERE l.group_id = :g AND l.status = 1"""


def _v_layout(d, ctx):
    _exigir(d, ("nome", "Nome do layout"), ("assentos", "Quantidade de assentos"))
    if not 1 <= int(_num(d["assentos"]) or 0) <= 100:
        raise Erro("assentos", "Quantidade de assentos entre 1 e 100.")
    img = d.get("imagem")
    if img and len(str(img)) > 1_500_000:
        raise Erro("imagem", "A imagem do layout deve ter no máximo 1 MB.")


TIPOS.update({
    "passageiro": Tipo("Passageiro", SQL_PASSAGEIRO, _v_passageiro, _u_passageiro),
    "centro_custo": Tipo("Centro de custo", SQL_CENTRO_CUSTO, _v_centro_custo, _u_nome("Já existe um centro de custo com este nome."),
                         pode_excluir=_excluir_centro_custo),
    "turno": Tipo("Turno", SQL_TURNO, _v_turno, _u_nome("Já existe um turno com este nome.")),
    "grupo_linhas": Tipo("Grupo de linhas", SQL_GRUPO_LINHAS, _v_grupo_linhas, _u_nome("Já existe um grupo de linhas com este nome.")),
    "rota": Tipo("Rota", SQL_ROTA, _v_rota),
    "layout_assentos": Tipo("Layout de assentos", SQL_LAYOUT, _v_layout, _u_nome("Já existe um layout com este nome.")),
})


def _tipo(t: str) -> Tipo:
    if t not in TIPOS:
        raise HTTPException(404, "Cadastro desconhecido.")
    return TIPOS[t]


def _limpo(v):
    from datetime import timedelta
    if isinstance(v, timedelta):
        return round(v.total_seconds() / 60)
    if isinstance(v, (datetime,)):
        return v.isoformat(timespec="minutes")
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


async def _banco(tipo: str, g: int) -> list[dict]:
    t = _tipo(tipo)
    if not t.sql:
        return []
    k = (tipo, g)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < CACHE_S:
        return hit[1]
    linhas = [{k2: _limpo(v) for k2, v in r.items()} for r in await _ler(t.sql, {"g": g})]
    if tipo == "cerca":
        for r in linhas:
            if r.get("geometria"):
                try:
                    gj = json.loads(r["geometria"])
                    coords = gj["coordinates"][0] if gj["type"] == "Polygon" else gj["coordinates"]
                    # As cercas guardam X = latitude (eixos trocados no sistema atual).
                    r["pontos"] = [[c[0], c[1]] for c in coords]
                except (ValueError, KeyError, IndexError, TypeError):
                    r["pontos"] = []
            r.pop("geometria", None)
    if tipo == "rota":
        for r in linhas:
            try:
                gj = json.loads(r.pop("trajeto_geojson") or "null")
                coords = gj["coordinates"] if gj and gj["type"] == "LineString" else []
                r["trajeto"] = [[round(c[1], 6), round(c[0], 6)] for c in coords]
            except (ValueError, KeyError, TypeError):
                r["trajeto"] = []
    _CACHE[k] = (time.time(), linhas)
    return linhas


def _overlay(tipo: str, g: int) -> list[dict]:
    with _trava, _con() as c:
        return [dict(r) for r in c.execute("SELECT * FROM registro WHERE tipo = ? AND group_id = ?", (tipo, g))]


async def _lista(tipo: str, g: int) -> list[dict]:
    banco = await _banco(tipo, g)
    ov = _overlay(tipo, g)
    por_origem = {o["origem_id"]: o for o in ov if o["origem_id"] is not None}
    out = []
    for r in banco:
        o = por_origem.get(r["id"])
        if o and o["excluido"]:
            continue
        linha = {**r, "id": str(r["id"]), "origem_id": r["id"], "origem": "banco", "provisorio": False}
        if o:
            linha.update(json.loads(o["dados"]))
            linha.update({"id": o["id"], "provisorio": True, "editado_em": o["em"]})
        out.append(linha)
    for o in ov:
        if o["origem_id"] is None and not o["excluido"]:
            out.append({**json.loads(o["dados"]), "id": o["id"], "origem_id": None, "origem": "plataforma", "provisorio": True, "editado_em": o["em"]})
    return out


async def _validar(tipo: str, g: int, dados: dict, user, ignorar_id: Optional[str]) -> list[str]:
    t = _tipo(tipo)
    ctx = {"avisos": [], "ss": bool(getattr(user, "is_super_admin", False)), "group_id": g}
    if tipo == "cerca":
        acc = await _ler('SELECT account_id FROM mova."group" WHERE id = :g', {"g": g})
        ctx["account_id"] = acc[0]["account_id"] if acc else None
    try:
        t.validar(dados, ctx)
    except Erro as e:
        raise HTTPException(422, {"message": e.msg, "field": e.campo})
    unicos = t.unicos(dados)
    if unicos:
        existentes = await _lista(tipo, g)
        extra: list[dict] = []
        if tipo == "usuario":  # login único no sistema todo
            extra = await _ler("SELECT id::text AS id, login FROM mova.users WHERE status = 1 AND lower(login) = lower(:l)", {"l": dados.get("login") or ""})
        if tipo == "dispositivo":  # unicidade entre dispositivos ativos de qualquer conta (regra do sistema atual)
            extra = await _ler("""SELECT id::text AS id, device_model_id AS modelo_id, identifier AS identificador, imei, number AS linha
                                  FROM mova.device WHERE status = 1 AND (upper(identifier) = upper(:i) OR (imei <> '' AND imei = :m)
                                  OR (number <> '' AND regexp_replace(number, '\\D', '', 'g') = :n))""",
                               {"i": dados.get("identificador") or "", "m": dados.get("imei") or "-", "n": _so_digitos(dados.get("linha")) or "-"})
        for campo, msg, chave in unicos:
            alvo = chave(dados)
            if alvo in (None, ""):
                continue
            for r in existentes + extra:
                if str(r.get("id")) in (str(ignorar_id), str(dados.get("_origem_id"))):
                    continue
                if chave(r) == alvo:
                    raise HTTPException(422, {"message": msg, "field": campo})
    return ctx["avisos"]


# ------------------------------------------------------------------ endpoints

@router.get("/{tipo}")
async def listar(tipo: str, group_id: int = Query(...), busca: Optional[str] = Query(None),
                 user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    lista = await _lista(tipo, group_id)
    if busca:
        b = busca.lower()
        lista = [r for r in lista if b in json.dumps(r, default=str, ensure_ascii=False).lower()]
    return {"data": lista, "total": len(lista), "provisorios": sum(1 for r in lista if r["provisorio"])}


class Gravacao(BaseModel):
    group_id: int
    dados: dict
    motivo: Optional[str] = None


@router.post("/{tipo}")
async def criar(tipo: str, g_: Gravacao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, g_.group_id)
    t = _tipo(tipo)
    if t.so_ss_cria and not getattr(user, "is_super_admin", False):
        raise HTTPException(403, f"Só a SS cria {t.nome.lower()}.")
    dados = {k: v for k, v in g_.dados.items() if k not in ("id", "origem", "origem_id", "provisorio", "editado_em")}
    avisos = await _validar(tipo, g_.group_id, dados, user, None)
    with _trava, _con() as c:
        n = c.execute("SELECT count(*) FROM registro WHERE tipo = ?", (tipo,)).fetchone()[0] + 1
        rid = f"n{n}"
        while c.execute("SELECT 1 FROM registro WHERE tipo = ? AND id = ?", (tipo, rid)).fetchone():
            n += 1
            rid = f"n{n}"
        c.execute("INSERT INTO registro (tipo, id, group_id, origem_id, dados, autor, em) VALUES (?,?,?,?,?,?,?)",
                  (tipo, rid, g_.group_id, None, json.dumps(dados, default=str), getattr(user, "user_id", None), _agora()))
        c.execute("INSERT INTO historico (tipo, registro_id, acao, dados, autor, em) VALUES (?,?,?,?,?,?)",
                  (tipo, rid, "create", json.dumps(dados, default=str), getattr(user, "user_id", None), _agora()))
    return {"id": rid, "avisos": avisos}


@router.put("/{tipo}/{rid}")
async def editar(tipo: str, rid: str, g_: Gravacao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, g_.group_id)
    lista = await _lista(tipo, g_.group_id)
    atual = next((r for r in lista if r["id"] == rid), None)
    if not atual:
        raise HTTPException(404, "Registro não encontrado.")
    dados = {**{k: v for k, v in atual.items() if k not in ("id", "origem", "provisorio", "editado_em")},
             **{k: v for k, v in g_.dados.items() if k not in ("id", "origem", "origem_id", "provisorio", "editado_em")}}
    dados["_origem_id"] = atual.get("origem_id")
    avisos = await _validar(tipo, g_.group_id, dados, user, rid)
    dados.pop("_origem_id", None)
    origem = atual.get("origem_id")
    with _trava, _con() as c:
        if atual["provisorio"]:
            c.execute("UPDATE registro SET dados = ?, autor = ?, em = ? WHERE tipo = ? AND id = ?",
                      (json.dumps(dados, default=str), getattr(user, "user_id", None), _agora(), tipo, rid))
            novo_id = rid
        else:
            novo_id = f"e{origem}"
            c.execute("INSERT OR REPLACE INTO registro (tipo, id, group_id, origem_id, dados, autor, em) VALUES (?,?,?,?,?,?,?)",
                      (tipo, novo_id, g_.group_id, origem, json.dumps(dados, default=str), getattr(user, "user_id", None), _agora()))
        c.execute("INSERT INTO historico (tipo, registro_id, acao, dados, motivo, autor, em) VALUES (?,?,?,?,?,?,?)",
                  (tipo, novo_id, "update", json.dumps(g_.dados, default=str), g_.motivo, getattr(user, "user_id", None), _agora()))
    return {"id": novo_id, "avisos": avisos}


@router.post("/{tipo}/{rid}/excluir")
async def excluir(tipo: str, rid: str, g_: Gravacao, user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, g_.group_id)
    t = _tipo(tipo)
    if tipo == "empresa":
        raise HTTPException(400, "A empresa não é excluída por aqui.")
    lista = await _lista(tipo, g_.group_id)
    atual = next((r for r in lista if r["id"] == rid), None)
    if not atual:
        raise HTTPException(404, "Registro não encontrado.")
    if t.exige_motivo and not (g_.motivo or "").strip():
        raise HTTPException(422, {"message": "Informe o motivo da exclusão.", "field": "motivo"})
    if t.pode_excluir:
        bloqueio = await t.pode_excluir(atual, {})
        if bloqueio:
            raise HTTPException(409, bloqueio)
    with _trava, _con() as c:
        if atual["origem"] == "plataforma":
            c.execute("UPDATE registro SET excluido = 1, motivo = ?, em = ? WHERE tipo = ? AND id = ?", (g_.motivo, _agora(), tipo, rid))
        else:
            c.execute("INSERT OR REPLACE INTO registro (tipo, id, group_id, origem_id, dados, excluido, motivo, autor, em) VALUES (?,?,?,?,?,1,?,?,?)",
                      (tipo, f"e{atual['origem_id']}", g_.group_id, atual["origem_id"], "{}", g_.motivo, getattr(user, "user_id", None), _agora()))
        c.execute("INSERT INTO historico (tipo, registro_id, acao, motivo, autor, em) VALUES (?,?,?,?,?,?)",
                  (tipo, rid, "delete", g_.motivo, getattr(user, "user_id", None), _agora()))
    return {"ok": True}


@router.get("/{tipo}/{rid}/historico")
async def historico(tipo: str, rid: str, user=Depends(require_permission("reports", "read"))):
    with _trava, _con() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM historico WHERE tipo = ? AND registro_id = ? ORDER BY id DESC", (tipo, rid))]
    return {"data": rows}


@router.get("/opcoes/todas")
async def opcoes(group_id: int = Query(...), user=Depends(require_permission("reports", "read"))):
    """Listas de apoio dos formulários (unidades, categorias, modelos, motoristas…)."""
    _grupo_ok(user, group_id)
    k = ("opcoes", group_id)
    hit = _CACHE.get(k)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    acc = (await _ler('SELECT account_id FROM mova."group" WHERE id = :g', {"g": group_id}) or [{"account_id": None}])[0]["account_id"]
    r = {
        "subgrupos": [{"id": x["id"], "nome": x["nome"]} for x in await _lista("subgrupo", group_id)],
        "tipos_veiculo": await _ler("SELECT id, name AS nome FROM mova.unit_type ORDER BY name", {}),
        "categorias": await _ler("SELECT id, name AS nome, unit_type_id AS tipo_id FROM mova.unit_category ORDER BY name", {}),
        "motoristas": await _ler("SELECT id, name AS nome FROM mova.driver WHERE group_id = :g AND status = 1 ORDER BY name", {"g": group_id}),
        "fabricantes": await _ler("SELECT id, name AS nome FROM mova.device_manufacturer WHERE status = 1 ORDER BY name", {}),
        "modelos": await _ler("SELECT id, name AS nome, device_manufacturer_id AS fabricante_id FROM mova.device_model WHERE status = 1 ORDER BY name", {}),
        "produtos": await _ler("SELECT id, code AS codigo, name AS nome FROM mova.products WHERE status = 1 ORDER BY id", {}),
        "operadoras": OPERADORAS,
        "motivos_remocao": await _ler("SELECT id, name AS nome FROM mova.tracked_unit_removed_reason ORDER BY name", {}),
        "categorias_cerca": await _ler("""SELECT cca.id, cc.nome FROM mova.cerca_category_account cca JOIN mova.cerca_category cc ON cc.id = cca.category_id
                                           WHERE cca.account_id = :a ORDER BY cc.nome""", {"a": acc}),
        "categorias_poi": await _ler("""SELECT pca.id, pc.nome FROM mova.poi_category_account pca JOIN mova.poi_category pc ON pc.id = pca.category_id
                                         WHERE pca.account_id = :a ORDER BY pc.nome""", {"a": acc}),
        "parametros_alarme": await _ler("""SELECT p.id, p.name AS nome, p.type AS tipo_id, t.operators AS operadores, t.operators_name AS nomes_operadores
                                            FROM mova.alarm_parameter p LEFT JOIN mova.alarm_parameter_type t ON t.id = p.type ORDER BY p.name""", {}),
        "eventos": await _ler("SELECT id, name AS nome FROM mova.tracker_event ORDER BY name", {}),
        "modalidades_linha": await _ler("SELECT id, name AS nome FROM mova.buss_line_modality ORDER BY id", {}),
        "veiculos": [{"id": x["origem_id"] or x["id"], "nome": " · ".join(v for v in (x.get("descricao"), x.get("placa")) if v)} for x in await _lista("veiculo", group_id)],
        "dispositivos": [{"id": x["origem_id"] or x["id"], "nome": f"{x.get('identificador')} ({x.get('modelo') or '—'})", "placa": x.get("placa")}
                         for x in await _lista("dispositivo", group_id)],
        "account_id": acc,
        "centros_custo": [{"id": x["origem_id"] or x["id"], "nome": x["nome"]} for x in await _lista("centro_custo", group_id)],
        "turnos": [{"id": x["origem_id"] or x["id"], "nome": x["nome"]} for x in await _lista("turno", group_id)],
        "linhas": [{"id": x["origem_id"] or x["id"], "nome": " · ".join(v for v in (x.get("nome"), x.get("descricao")) if v)}
                   for x in await _lista("linha", group_id)],
    }
    _CACHE[k] = (time.time(), r)
    return r
