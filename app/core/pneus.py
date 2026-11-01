"""Gestão de pneus sem sensores (documento do PM, 01/11/2026): regras sem banco. MVP: US-01 a US-08 e US-01B.

Só o status Montado soma km. A vida sobe apenas no retorno aprovado da recapagem. Km informado pelo usuário (anterior à SS)
fica sempre separado do km medido pela plataforma.
"""

from datetime import date
from typing import Optional

ESTOQUE, MONTADO, RECAPAGEM, REPARO, DESCARTADO = "estoque", "montado", "recapagem", "reparo", "descartado"
STATUS = (ESTOQUE, MONTADO, RECAPAGEM, REPARO, DESCARTADO)
STATUS_ROTULO = {ESTOQUE: "Em estoque", MONTADO: "Montado", RECAPAGEM: "Em recapagem", REPARO: "Em reparo", DESCARTADO: "Descartado"}
#: Provisório: o documento deixa o padrão para validar com clientes (dúvida 7). Cada empresa troca o seu.
LIMITE_RECAPAGENS_PADRAO = 3
DIAS_KM_DESATUALIZADO = 7
MOTIVOS_SAIDA = ("Desgaste", "Rodízio", "Ocorrência", "Enviado para recapagem", "Enviado para reparo", "Venda ou baixa do veículo", "Troca de medida", "Outro")
TIPOS_OCORRENCIA = ("Estouro", "Furo", "Corte lateral", "Bolha ou hérnia", "Desgaste irregular", "Desgaste prematuro", "Separação de banda", "Dano por impacto", "Outro")
RESULTADOS_OCORRENCIA = ("Reparado", "Segue em uso", "Enviado para recapagem", "Descartado")
GRAVIDADES = ("leve", "media", "grave")

MODELOS_EIXOS = {
    "2_eixos": ("2 eixos", (False, True)),
    "3_eixos": ("3 eixos", (False, True, True)),
    "cavalo": ("Cavalo mecânico", (False, True, True)),
    "carreta_3": ("Carreta de 3 eixos", (True, True, True)),
}


def posicoes(modelo: str) -> list[dict]:
    """Posições do modelo de eixos, com estepe. O cadastro de veículos não tem eixos (conferido em 01/11/2026)."""
    if modelo not in MODELOS_EIXOS:
        raise ValueError("Modelo de eixos inexistente.")
    out = []
    for i, duplo in enumerate(MODELOS_EIXOS[modelo][1], 1):
        for lado, sigla in (("esquerdo", "E"), ("direito", "D")):
            for roda, s in (("interno", "I"), ("externo", "X")) if duplo else ((None, ""),):
                out.append({"chave": f"{i}{sigla}{s}", "rotulo": f"{i}º eixo {lado}" + (f" {roda}" if roda else "")})
    out.append({"chave": "ESTEPE", "rotulo": "Estepe"})
    return out


def rotulo_posicao(chave: Optional[str]) -> Optional[str]:
    if not chave:
        return None
    if chave == "ESTEPE":
        return "Estepe"
    lado = "esquerdo" if chave[1] == "E" else "direito"
    roda = {"I": " interno", "X": " externo"}.get(chave[2:], "")
    return f"{chave[0]}º eixo {lado}{roda}"


def erro_cadastro(preco: Optional[float], compra_em: Optional[date], hoje: date, carga_inicial: bool = False) -> Optional[str]:
    if preco is None and not carga_inicial:
        return "Informe o preço pago."
    if preco is not None and preco <= 0:
        return "O preço pago precisa ser maior que zero."
    if compra_em is None and not carga_inicial:
        return "Informe a data da compra."
    if compra_em is not None and compra_em > hoje:
        return "A data da compra não pode ser futura."
    return None


def erro_data(d: date, hoje: date, depois_de: Optional[date] = None) -> Optional[str]:
    if d > hoje:
        return "A data não pode ser futura."
    if depois_de and d < depois_de:
        return f"A data não pode ser anterior a {depois_de.strftime('%d/%m/%Y')}."
    return None


def recusa_status(status: str, acao: str, onde: str = "", desde: str = "") -> Optional[str]:
    """Por que o pneu não pode fazer a ação agora; None se pode."""
    if status == DESCARTADO:
        return "Pneu descartado não muda mais."
    if acao == "montar" or acao == "recapagem" or acao == "reparo":
        if status == MONTADO:
            return f"O pneu já está montado em {onde}."
        if status == RECAPAGEM:
            return f"O pneu está no fornecedor de recapagem desde {desde}."
        if status == REPARO:
            return f"O pneu está em reparo desde {desde}."
    if acao == "desmontar" and status != MONTADO:
        return "O pneu não está montado."
    return None


def recapagens_feitas(vida: int) -> int:
    return max(vida - 1, 0)


def limite_atingido(vida: int, limite: int) -> bool:
    return recapagens_feitas(vida) >= limite


def km_montagem(entrada: float, saida: Optional[float]) -> tuple[float, bool]:
    """(km rodado, inconsistente). Km negativo nunca aparece: vira 0 e é sinalizado."""
    if saida is None:
        return 0.0, False
    if saida < entrada:
        return 0.0, True
    return saida - entrada, False


def resumo_km(montagens: list[dict], km_atual: Optional[float], km_anterior: float = 0) -> dict:
    """Km por montagem e por vida. Montagem aberta usa o km atual do veículo; encerrada nunca muda."""
    medido, inconsistente, por_vida = 0.0, False, {}
    detalhes = []
    for m in montagens:
        fim = m["km_saida"] if m.get("saiu_em") else km_atual
        km, ruim = km_montagem(m["km_entrada"], fim)
        inconsistente = inconsistente or ruim
        medido += km
        v = por_vida.setdefault(m["vida"], {"km": 0.0, "montagens": 0})
        v["km"] += km
        v["montagens"] += 1
        detalhes.append({**m, "km": round(km), "inconsistente": ruim})
    return {"km_medido": round(medido), "km_informado": round(km_anterior or 0), "km_total": round(medido + (km_anterior or 0)),
            "inconsistente": inconsistente, "por_vida": por_vida, "montagens": detalhes}


def km_desatualizado(leitura: Optional[date], hoje: date) -> bool:
    return leitura is None or (hoje - leitura).days > DIAS_KM_DESATUALIZADO


def fonte_do_km(can_total_odometer: Optional[float]) -> str:
    """Com odômetro do painel, a plataforma bate com ele (1.094 de 1.122 veículos com os dois, até 2%, em 01/11/2026)."""
    valido = can_total_odometer is not None and can_total_odometer not in (0, 100_000_000)
    return "Odômetro do painel" if valido else "Rastreador (sem odômetro do painel)"


def km_suspeito(km: float, referencia: Optional[float]) -> bool:
    """Km menor que o já registrado do veículo pede confirmação (RN-08). Tolerância de 1 km para arredondamento."""
    return referencia is not None and km < referencia - 1
