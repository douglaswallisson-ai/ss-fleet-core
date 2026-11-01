"""Multas de exemplo do quadro (pedido do PM, 01/11/2026), enquanto o e-Frotas não está ligado.

Tudo aqui é fictício: placas que não existem na frota, motorista e telemetria inventados. Só usuários da SS veem, cada
card leva a marca "Exemplo", e tudo é apagado na primeira sincronização real.
"""

from datetime import date, timedelta

from app.core.multas import RegistroOficial

PREFIXO = "EXEMPLO-"
_PIX = "00020126580014br.gov.bcb.pix0136exemplo-nao-pagar-0000-000000000000520400005303986540{v}5802BR5907EXEMPLO6009SAO PAULO6304ABCD"
_BARRAS = "23793.38128 60000.000003 00000.000400 1 000000000{v}"

_MULTAS = (
    # ait, placa, status, uf, dias atrás, órgão, infração, local, valor, pontos, recurso em, pagamento em, motorista, leitura
    ("1", "EXE-0A01", "autuacao", "MG", 6, "DER-MG", "Excesso de velocidade até 20%", "Ibiá", 130.16, 4, 5, 40, "Motorista Exemplo 1",
     (-19.4772, -46.5387, "BR-262, Ibiá - Minas Gerais", 74, 1820, "VERDE")),
    ("2", "EXE-0A02", "autuacao", "SP", 3, "DER-SP", "Transitar em faixa exclusiva", "Campinas", 293.47, 7, 25, 45, None,
     (-22.9056, -47.0608, "Av. Norte-Sul, Campinas - São Paulo", 38, 1450, "EXTRA ECONÔMICA")),
    ("3", "EXE-0A03", "infracao", "MG", 30, "PRF", "Avanço de sinal vermelho", "São João del-Rei", 293.47, 7, 20, 3, "Motorista Exemplo 2",
     (-21.1361, -44.2619, "Av. Leite de Castro, São João del-Rei - Minas Gerais", 41, 2010, "AMARELA")),
    ("4", "EXE-0A04", "infracao", "RJ", 45, "DETRAN-RJ", "Estacionar em local proibido", "Rio de Janeiro", 88.38, 3, 12, 30, "Motorista Exemplo 3",
     (-22.9068, -43.1729, "Av. Presidente Vargas, Rio de Janeiro", 0, 0, "RPM 0")),
    ("5", "EXE-0A05", "paga", "SP", 80, "DETRAN-SP", "Excesso de velocidade até 20%", "São Paulo", 130.16, 4, None, None, "Motorista Exemplo 1",
     (-23.5505, -46.6333, "Marginal Tietê, São Paulo", 92, 1980, "VERDE")),
    ("6", "EXE-0A06", "cancelada", "PR", 95, "DER-PR", "Ultrapassagem proibida", "Curitiba", 195.23, 5, None, None, None,
     (-25.4284, -49.2733, "BR-277, Curitiba - Paraná", 66, 1700, "VERDE")),
)


def registros(hoje: date) -> list[RegistroOficial]:
    def dia(d):
        return (hoje + timedelta(days=d)).isoformat() if d is not None else None

    return [RegistroOficial(
        ait=PREFIXO + m[0], placa=m[1], status=m[2], uf=m[3], autuada_em=f"{(hoje - timedelta(days=m[4])).isoformat()}T10:30:00",
        orgao=m[5], infracao=m[6], local=m[7], valor=m[8], pontos=m[9], prazo_recurso=dia(m[10]), prazo_pagamento=dia(m[11]),
        codigo_barras=_BARRAS.format(v=f"{int(m[8] * 100):05d}") if m[11] is not None else None,
        pix=_PIX.format(v=f"{m[8]:.2f}") if m[11] is not None else None) for m in _MULTAS]


def motoristas() -> dict[str, dict]:
    return {PREFIXO + m[0]: {"motorista": m[12]} for m in _MULTAS}


def leitura(ait: str, autuada_em: str) -> dict | None:
    for m in _MULTAS:
        if PREFIXO + m[0] == ait:
            lat, lng, endereco, vel, rpm, faixa = m[13]
            return {"local_time": autuada_em, "latitude": lat, "longitude": lng, "address": endereco, "speed": vel, "can_speed": vel, "rpm": rpm, "faixa": faixa}
    return None
