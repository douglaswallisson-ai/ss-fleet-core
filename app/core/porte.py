"""
Porte do veículo de carga pelo nome do modelo (PM, 13/10/2026: no benchmark de consumo a CECOTI,
com caminhões leves, não pode ser comparada com quem roda carreta).

O cadastro não diz o porte (quase toda a carga está como categoria 3 "Pesados" e `model` vazio),
mas o nome do modelo brasileiro carrega o peso bruto: VW 8.160 = 8 t, MB Atego 1719 = 17 t,
VW 24.280 = 24 t; cavalo mecânico pela linha (Volvo FH, Scania R/G, Iveco Stralis, MB Axor/Actros…).
Fonte do nome: `vehicle_model.nome` e, sem ele, a descrição do veículo (`tracked_unit.label2`).

SUPOSIÇÃO: faixas de peso bruto — leve até 10 t (VUC, 3/4, HR, Delivery, Accelo), médio de 11 a
19 t (toco), semipesado de 20 a 26 t (truck 6x2/6x4 rígido), pesado = cavalo mecânico. Sem como
classificar, fica "não classificado" e não entra na comparação de consumo por porte.
"""

import re
from typing import Optional

PORTES = ("leve", "medio", "semipesado", "pesado")
NOME_PORTE = {
    "leve": "Leves (VUC, 3/4)",
    "medio": "Médios (toco)",
    "semipesado": "Semipesados (truck)",
    "pesado": "Pesados (cavalo mecânico)",
}

_LEVE = ("HR", "BONGO", "DAILY", "DELIVERY", "ACCELO", "EXPRESS", "SPRINTER", "MASTER", "DUCATO", "IVECO 35", "VUC", "3/4")
_PESADO = (
    "FH", "FM ", "FMX", "NH", "XF", "CF ", "STRALIS", "S-WAY", "S WAY", "HI-WAY", "ACTROS", "AXOR", "AROCS",
    "METEOR", "TGX", "CAVALO", "TRATOR", "R 4", "R4", "R 5", "R5", "G 3", "G3", "G 4", "G4", "P 3", "P3",
)


def porte_do_modelo(*nomes: Optional[str]) -> Optional[str]:
    """Primeiro nome que der para classificar (modelo do cadastro, depois a descrição do veículo)."""
    for nome in nomes:
        p = _porte(nome)
        if p:
            return p
    return None


def _porte(nome: Optional[str]) -> Optional[str]:
    if not nome:
        return None
    n = " " + re.sub(r"\s+", " ", nome.upper().replace("_", " ")) + " "
    # Peso bruto no padrão VW/MAN (8.160, 24-280, 26.320) ou MB/Ford de 4 dígitos (1719, 2426, 815).
    pbt: Optional[int] = None
    m = re.search(r"(?<![\d.])(\d{1,2})[.\-](\d{3})(?!\d)", n)
    if m:
        pbt = int(m.group(1))
    else:
        m = re.search(r"(?<![\d.])(\d{3,4})(?![\d.])", n)
        if m and m.group(1)[-2:].isdigit():
            v = m.group(1)
            t = int(v[:-2])
            if 3 <= t <= 33:
                pbt = t
    # Cavalo mecânico: linha de cavalo, sufixo de cavalo no MB (2544 S, 2546 LS) ou "T" do Iveco/Volvo (8X2T, 600S44T).
    k_ = re.sub(r"[/]", " ", n)  # "DAF/XF105" → "DAF XF105"
    cavalo = any(f" {k}" in k_ for k in _PESADO) or re.search(r"\d{4} ?L?S\b", n) or re.search(r"\dX\dT\b|S\d{2}T\b", n)
    if cavalo:
        return "pesado"
    if any(f" {k}" in k_ for k in _LEVE):
        return "leve"
    if pbt is None:
        return None
    if pbt <= 10:
        return "leve"
    if pbt <= 19:
        return "medio"
    if pbt <= 26:
        return "semipesado"
    return "pesado"
