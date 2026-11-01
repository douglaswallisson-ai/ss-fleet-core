"""Quadro de multas: status oficial, auditoria e falha da integração, em um arquivo temporário."""

import asyncio

import pytest

from app.api.v1.endpoints import multas as ep
from app.core import multas as mu, multas_efrotas


@pytest.fixture(autouse=True)
def arquivo(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "ARQUIVO", tmp_path / "multas.sqlite")


def _reg(status):
    return mu.RegistroOficial(ait="A1", placa="ABC-1D23", status=status, uf="MG", autuada_em="2026-10-01T10:00:00", valor=130.16)


def _multa():
    with ep._con() as c:
        return dict(c.execute("SELECT * FROM multa").fetchone())


def _trilha():
    with ep._con() as c:
        return [(a["origem"], a["de"], a["para"]) for a in c.execute("SELECT * FROM multa_auditoria ORDER BY id")]


def test_mudanca_oficial_tira_da_recorrida_e_fica_na_auditoria():
    ep.aplicar_sincronizacao([_reg("infracao")], {}, {})
    with ep._con() as c:
        c.execute("UPDATE multa SET coluna = 'recorrida'")

    ep.aplicar_sincronizacao([_reg("infracao")], {}, {})
    assert _multa()["coluna"] == "recorrida"

    ep.aplicar_sincronizacao([_reg("paga")], {}, {})
    assert _multa()["coluna"] == "paga"
    assert _trilha() == [("oficial", None, "infracao"), ("oficial", "recorrida", "paga")]


def test_status_desconhecido_nao_entra():
    assert ep.aplicar_sincronizacao([_reg("em_analise")], {}, {})["recusados"] == 1
    with ep._con() as c:
        assert c.execute("SELECT count(*) FROM multa").fetchone()[0] == 0


def test_falha_avisa_e_mantem_a_ultima_sincronizacao_e_os_dados(monkeypatch):
    async def ok():
        return [_reg("autuacao")]

    async def veiculos(_):
        return {}

    async def falha():
        raise RuntimeError("fora do ar")

    monkeypatch.setattr(ep, "_veiculos_por_placa", veiculos)
    monkeypatch.setattr(multas_efrotas, "buscar", ok)
    asyncio.run(ep.sincronizar())
    with ep._con() as c:
        ultima = c.execute("SELECT ultima_em FROM multa_sincronizacao").fetchone()[0]
    assert ultima

    monkeypatch.setattr(multas_efrotas, "buscar", falha)
    asyncio.run(ep.sincronizar())
    with ep._con() as c:
        s = c.execute("SELECT situacao, ultima_em FROM multa_sincronizacao").fetchone()
    assert (s["situacao"], s["ultima_em"]) == ("falha", ultima)
    assert _multa()["ait"] == "A1"


def test_exemplos_so_para_a_ss_e_somem_na_primeira_sincronizacao_real(monkeypatch):
    asyncio.run(ep.sincronizar())
    with ep._con() as c:
        exemplos = [dict(r) for r in c.execute(ep.SQL_MULTA)]
    assert len(exemplos) == 6 and all(m["group_id"] is None for m in exemplos)

    class Usuario:
        def __init__(self, ss):
            self.is_super_admin = ss

    assert ep._visivel(Usuario(True), [3076], exemplos[0])
    assert not ep._visivel(Usuario(False), [3076], exemplos[0])

    async def real():
        return [_reg("autuacao")]

    async def veiculos(_):
        return {}

    monkeypatch.setattr(ep, "_veiculos_por_placa", veiculos)
    monkeypatch.setattr(multas_efrotas, "buscar", real)
    asyncio.run(ep.sincronizar())
    with ep._con() as c:
        assert [r["ait"] for r in c.execute("SELECT ait FROM multa")] == ["A1"]


def test_sem_contrato_a_situacao_e_nao_configurada():
    asyncio.run(ep.sincronizar())
    with ep._con() as c:
        s = c.execute("SELECT situacao, ultima_em FROM multa_sincronizacao").fetchone()
    assert s["situacao"] == "nao_configurada" and s["ultima_em"] is None
