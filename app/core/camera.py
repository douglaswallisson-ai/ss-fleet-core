"""
Classificação das ocorrências de câmera (`vcms.vcms_history`).

Usado pelo Painel CCO e pela Videotelemetria, para que as duas telas chamem o
mesmo evento pelo mesmo nome e com a mesma gravidade.

O nome do tipo sai de `vcms_alarm_type` pela chave (type, device_model_id,
source). `alarm_type` sozinho NÃO identifica o tipo: o número se repete entre
modelos e, no mesmo modelo, entre a câmera do motorista (DMS) e a da estrada
(ADAS). Ligando só pelo id, 3.548 ocorrências de "Veículo Muito Próximo à
Frente" da semana até 06/10/2026 saíam como "Motorista Fumando".
"""

import re
import unicodedata

#: `alarm_source` da câmera voltada ao motorista e da voltada à estrada, por
#: modelo (114: 65/64, 122: 265/264, 156 e 157: 101/100).
SOURCES_DMS = (65, 265, 101)
SOURCES_ADAS = (64, 264, 100)

#: JOIN do nome do tipo; `h` é `vcms_history`, `t` o tipo.
JOIN_TIPO = (
    "LEFT JOIN vcms.vcms_alarm_type t ON t.type = h.alarm_type"
    " AND t.device_model_id = h.device_model_id AND t.source = h.alarm_source"
)
#: Nome a exibir. Tipo fora do catálogo (ex.: -1, 88) não some da lista.
NOME_SQL = "coalesce(t.name, 'Evento de câmera não catalogado (tipo ' || h.alarm_type || ')')"

CAM_CRITICO = ("fadiga", "celular", "olhos fechados", "colis", "frenagem autom", "embriaguez", "alcool", "álcool")
CAM_EQUIPAMENTO = ("obstru", "imagem com exce", "óculos bloqueadores", "oculos bloqueadores")
#: Registros que não são infração (captura, identificação, sinalização lida).
CAM_INFORMATIVO = ("captura autom", "troca de motorista", "reconhecimento", "placa de transito detectada", "semaforo detectado")

#: Nome do catálogo → tipo que a tela conhece (TipoAlarmeVideo no front).
#: Primeiro trecho que casar vence ("Frenagem Automática" não é risco de colisão).
_TIPOS_TELA = (
    ("olhos fechados", "olhos_fechados"),
    ("fadiga", "fadiga"),
    ("distra", "distracao"),
    ("bocejo", "bocejo"),
    ("celular", "celular"),
    ("fumando", "fumando"),
    ("frenagem autom", "colisao"),
    ("risco de colis", "risco_colisao"),
    ("muito proximo", "proximidade_dianteira"),
    ("distancia insuficiente", "proximidade_dianteira"),
    ("monitoramento de distancia", "proximidade_dianteira"),
    ("excesso de velocidade", "excesso_velocidade"),
)


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def gravidade_camera(nome: str) -> tuple[str, str]:
    """(fonte, gravidade) de um evento de câmera pelo nome. Tabela aprovada pelo PM em 06/10/2026."""
    n = (nome or "").lower()
    if any(t in n for t in CAM_EQUIPAMENTO):
        return "equipamento", "moderado"
    if any(t in n for t in CAM_CRITICO):
        return "camera", "critico"
    return "camera", "moderado"


def severidade_video(nome: str) -> str:
    """critical / warning / info, no vocabulário da tela de Videotelemetria."""
    if any(t in _sem_acento((nome or "").lower()) for t in CAM_INFORMATIVO):
        return "info"
    return "critical" if gravidade_camera(nome)[1] == "critico" else "warning"


def tipo_video(nome: str) -> str:
    """Tipo da tela para o nome do catálogo; sem equivalente, o nome em forma de chave."""
    n = _sem_acento((nome or "").lower())
    for trecho, tipo in _TIPOS_TELA:
        if trecho in n:
            return tipo
    return re.sub(r"[^a-z0-9]+", "_", n).strip("_") or "outro"


def categoria_sql() -> str:
    """CASE SQL de categoria (equipamento, dms, adas, outro) — filtro e contadores no banco."""
    equip = " OR ".join(f"lower(t.name) LIKE '%{t}%'" for t in CAM_EQUIPAMENTO)
    return (
        f"CASE WHEN {equip} THEN 'equipamento'"
        f" WHEN h.alarm_source IN ({', '.join(map(str, SOURCES_DMS))}) THEN 'dms'"
        f" WHEN h.alarm_source IN ({', '.join(map(str, SOURCES_ADAS))}) THEN 'adas'"
        " ELSE 'outro' END"
    )
