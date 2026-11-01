"""Conector do e-Frotas (Senatran/Serpro) para o quadro de multas.

Ainda sem contrato, certificado e-CNPJ nem documentação técnica: não há formato para ler, então nada aqui é inventado.
Quando o acesso sair, `buscar` passa a chamar o serviço e traduzir cada multa para `RegistroOficial`; o resto do quadro
(sincronização, auditoria, avisos) não muda.
"""

from app.core.multas import RegistroOficial


class IntegracaoNaoConfigurada(Exception):
    pass


async def buscar() -> list[RegistroOficial]:
    raise IntegracaoNaoConfigurada()
