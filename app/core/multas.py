"""Quadro de multas (pedido do PM, 01/11/2026): regras sem banco.

O status oficial manda: cada multa fica na coluna do status que vem do órgão. A única etapa do gestor é "Recorrida",
que vale enquanto o status oficial não mudar. Paga e Cancelada só entram pelo órgão.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

COLUNAS = ("autuacao", "infracao", "recorrida", "paga", "cancelada")
OFICIAIS = ("autuacao", "infracao", "paga", "cancelada")
ETAPAS_INTERNAS = ("recorrida",)
FINAIS = ("paga", "cancelada")
ROTULOS = {"autuacao": "Autuação", "infracao": "Infração", "recorrida": "Recorrida", "paga": "Paga", "cancelada": "Cancelada"}
AVISO_SO_OFICIAL = "Aguardando confirmação do órgão oficial"
DIAS_DESTAQUE = 7


@dataclass(frozen=True)
class RegistroOficial:
    """Uma multa como o conector entrega: já traduzida do formato do e-Frotas."""

    ait: str
    placa: str
    status: str  # um de OFICIAIS
    uf: str  # UF do órgão autuador
    autuada_em: str  # ISO, horário local
    orgao: Optional[str] = None
    infracao: Optional[str] = None
    local: Optional[str] = None
    valor: Optional[float] = None
    prazo_recurso: Optional[str] = None
    prazo_pagamento: Optional[str] = None
    pontos: Optional[int] = None
    codigo_barras: Optional[str] = None
    pix: Optional[str] = None


def validar(r: RegistroOficial) -> Optional[str]:
    """Motivo para recusar o registro; None se estiver bom. Status desconhecido nunca vira coluna por chute."""
    if r.status not in OFICIAIS:
        return f"status oficial desconhecido: {r.status}"
    if not r.ait.strip() or not r.placa.strip():
        return "sem AIT ou placa"
    return None


def coluna_ao_sincronizar(atual: Optional[str], oficial_antes: Optional[str], oficial: str) -> str:
    """Coluna depois de receber o status oficial. "Recorrida" só sobrevive se o órgão não mudou nada."""
    if atual == "recorrida" and oficial_antes == oficial and oficial not in FINAIS:
        return "recorrida"
    return oficial


def recusa_movimento(de: str, para: str) -> Optional[str]:
    """Por que o gestor não pode mover o card; None se pode."""
    if para in FINAIS:
        return AVISO_SO_OFICIAL
    if para not in COLUNAS:
        return "Coluna inexistente."
    if de == para:
        return "O card já está nesta coluna."
    if de in FINAIS:
        return f"Multa {ROTULOS[de].lower()} não muda de etapa."
    if para not in ETAPAS_INTERNAS:
        return "Esta coluna segue o status do órgão oficial."
    return None


def dias_ate(prazo: Optional[str], hoje: date) -> Optional[int]:
    return (date.fromisoformat(prazo[:10]) - hoje).days if prazo else None


def prazo_em_destaque(prazo: Optional[str], coluna: str, hoje: date) -> bool:
    """Vence em até 7 dias, ou já venceu. Multa paga ou cancelada não tem mais prazo a cuidar."""
    d = dias_ate(prazo, hoje)
    return d is not None and d <= DIAS_DESTAQUE and coluna not in FINAIS


def chave_de_ordem(m: dict) -> tuple:
    """Prazo mais próximo primeiro; sem prazo vai para o fim."""
    prazos = [p[:10] for p in (m.get("prazo_recurso"), m.get("prazo_pagamento")) if p]
    return (not prazos, min(prazos) if prazos else "", m.get("autuada_em") or "")


def montar_quadro(multas: list[dict]) -> list[dict]:
    """Colunas na ordem do pedido, com quantidade, valor total e cards ordenados."""
    out = []
    for c in COLUNAS:
        cards = sorted((m for m in multas if m["coluna"] == c), key=chave_de_ordem)
        out.append({"chave": c, "rotulo": ROTULOS[c], "interna": c in ETAPAS_INTERNAS, "quantidade": len(cards),
                    "valor_total": round(sum(m.get("valor") or 0 for m in cards), 2), "cards": cards})
    return out
