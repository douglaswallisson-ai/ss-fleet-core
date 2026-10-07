"""
Regras da manutenção (endpoints/manutencao.py) — testes de unidade, SEM banco.

Cada caso reproduz uma situação real que deu errado ou foi conferida no banco
(ficha: app/api/v1/endpoints/_docs/manutencao.md). Não usam as fixtures `db` e
`client` do conftest, que apontam para o banco de produção.

Rodar: .venv\\Scripts\\python.exe -m pytest tests/test_regras_manutencao.py -q
"""

from datetime import date, datetime, timedelta

from app.api.v1.endpoints.manutencao import _alertas_do_veiculo, _situacao_item, _suspeitos

AGORA = datetime.now()


def veiculo(**kw):
    """Última leitura recente do veículo (dentro das 6 h exigidas)."""
    return {"local_time": AGORA - timedelta(minutes=5), "temp": 85.0, "voltage": 28.4, "rpm": 800.0, **kw}


def hist(**kw):
    """Resumo das últimas 24 h, com o mínimo de leituras para valer."""
    base = {"oleo_todas": 200, "oleo_valores": 40, "oleo_max": 200.0, "oleo_max_7d": 200.0,
            "oleo_n": 50, "oleo_med": 180.0, "oleo_n_alto": 50, "oleo_med_alto": 150.0,
            "v_lig_n": 300, "v_lig_med": 28.4, "v_desl_n": 300, "v_desl_med": 26.0,
            "arla_n": 300, "arla_total": 300, "arla_codigo": 0, "arla_baixo": 0, "arla_cheio": 0, "arla_med": 60.0}
    base.update(kw)
    return base


def chaves(alertas):
    return sorted(a["chave"] for a in alertas)


# ------------------------------------------------------------ veículo normal

def test_veiculo_normal_sem_alerta_nem_suspeito():
    v, h = veiculo(), hist()
    assert _alertas_do_veiculo(v, h) == []
    assert _suspeitos(v, h) == []


def test_sinal_antigo_nao_gera_alerta():
    """Última leitura com mais de 6 h: não dá para afirmar nada do estado atual."""
    v = veiculo(local_time=AGORA - timedelta(hours=7), temp=120.0)
    assert _alertas_do_veiculo(v, hist(v_desl_med=19.0)) == []


# ------------------------------------------------------------------- óleo

def test_oleo_travado_e_suspeito_e_nao_alerta():
    """CECOTI 05/10/2026: 20 VW com 44 kPa idênticos em centenas de leituras."""
    v, h = veiculo(), hist(oleo_valores=1, oleo_max=44.0, oleo_max_7d=44.0, oleo_med=44.0, oleo_med_alto=44.0)
    assert [s["sinal"] for s in _suspeitos(v, h)] == ["oleo"]
    assert "oleo" not in chaves(_alertas_do_veiculo(v, h))


def test_oleo_baixo_com_escala_estourando_e_inconclusivo():
    """RCA: ~30 kPa em marcha lenta, mas o mesmo sensor chega a 240+ na semana (estouro de 255)."""
    v, h = veiculo(), hist(oleo_med=30.0, oleo_max_7d=252.0)
    assert [s["sinal"] for s in _suspeitos(v, h)] == ["oleo_escala"]
    assert "oleo" not in chaves(_alertas_do_veiculo(v, h))


def test_oleo_baixo_coerente_vira_alerta_critico():
    """Sensor que nunca passa de 120 kPa: leitura baixa é confiável."""
    v, h = veiculo(), hist(oleo_med=40.0, oleo_max=120.0, oleo_max_7d=120.0, oleo_med_alto=110.0)
    a = [x for x in _alertas_do_veiculo(v, h) if x["chave"] == "oleo"]
    assert len(a) == 1 and a[0]["nivel"] == "critico"


def test_oleo_com_poucas_leituras_nao_alerta():
    v, h = veiculo(), hist(oleo_med=40.0, oleo_n=3, oleo_max_7d=120.0)
    assert "oleo" not in chaves(_alertas_do_veiculo(v, h))


# ------------------------------------------------------------------ tensão

def test_bateria_fraca_em_repouso_24v_e_atencao():
    """RTR-8I02: ~23 V parado com o alternador carregando 28,6 V."""
    v, h = veiculo(), hist(v_desl_med=23.0, v_lig_med=28.6)
    a = [x for x in _alertas_do_veiculo(v, h) if x["chave"] == "bateria"]
    assert len(a) == 1 and a[0]["titulo"] == "Bateria fraca em repouso" and a[0]["nivel"] == "atencao"


def test_tensao_no_limite_nao_alerta_folga_de_medicao():
    """24,1–24,3 V em repouso e 25,9 V carregando apareciam como problema; a medição é no rastreador."""
    v = veiculo()
    assert _alertas_do_veiculo(v, hist(v_desl_med=24.2)) == []
    assert _alertas_do_veiculo(v, hist(v_lig_med=25.9)) == []


def test_alternador_sem_carregar():
    v, h = veiculo(), hist(v_lig_med=24.5)
    a = [x for x in _alertas_do_veiculo(v, h) if x["chave"] == "bateria"]
    assert a and a[0]["titulo"] == "Alternador sem carregar"


def test_sistema_12v_usa_limites_de_12v():
    """VW Express 12 V parado em 12,3 V é normal (não pode usar o limite de 24 V)."""
    v = veiculo(voltage=12.3)
    assert _alertas_do_veiculo(v, hist(v_lig_med=13.9, v_desl_med=12.3)) == []


def test_tensao_zero_e_suspeito_e_nao_alerta():
    """TEG-2C64: última leitura 0 V com o motor carregando 28,6 V nas 24 h."""
    v, h = veiculo(voltage=0.0), hist()
    assert "bateria" in [s["sinal"] for s in _suspeitos(v, h)]
    assert "bateria" not in chaves(_alertas_do_veiculo(v, h))


# -------------------------------------------------------------------- ARLA

def test_arla_com_codigo_102_e_suspeito_e_nao_alerta():
    """TDP-2E24: 102% (sem informação) quase o dia todo e um 5% solto — o nível real era 41%."""
    v, h = veiculo(), hist(arla_codigo=260, arla_total=273, arla_n=13, arla_baixo=13, arla_med=5.0)
    assert [s["titulo"] for s in _suspeitos(v, h)] == ["ARLA sem leitura válida"]
    assert "arla" not in chaves(_alertas_do_veiculo(v, h))


def test_arla_pulando_entre_vazio_e_cheio_e_suspeito():
    v, h = veiculo(), hist(arla_baixo=86, arla_cheio=12, arla_med=2.0)
    assert [s["sinal"] for s in _suspeitos(v, h)] == ["arla"]
    assert "arla" not in chaves(_alertas_do_veiculo(v, h))


def test_arla_baixo_coerente_vira_alerta():
    v, h = veiculo(), hist(arla_med=6.0)
    a = [x for x in _alertas_do_veiculo(v, h) if x["chave"] == "arla"]
    assert len(a) == 1 and a[0]["nivel"] == "atencao"


# ------------------------------------------------------------ temperatura

def test_pico_de_temperatura_sob_carga_nao_e_alerta():
    """RVB-3E31, 07/10/2026: última leitura 96 °C, mediana de 30 min 92 °C — pico normal, não "Motor quente"."""
    assert _alertas_do_veiculo(veiculo(temp=96.0), hist(temp_n=15.0, temp_med=92.0, temp_n_crit=0.0)) == []


def test_temperatura_atencao_e_critico():
    """Vale a mediana dos últimos 30 min com o motor ligado; crítico com 2 leituras de 107 °C ou mais."""
    assert [a["nivel"] for a in _alertas_do_veiculo(veiculo(temp=101.0), hist(temp_n=12.0, temp_med=101.0, temp_n_crit=0.0))] == ["atencao"]
    assert [a["nivel"] for a in _alertas_do_veiculo(veiculo(temp=108.0), hist(temp_n=12.0, temp_med=104.0, temp_n_crit=3.0))] == ["critico"]
    # Poucas leituras na janela: não dá para afirmar.
    assert _alertas_do_veiculo(veiculo(temp=108.0), hist(temp_n=2.0, temp_med=108.0, temp_n_crit=2.0)) == []


def test_arla_com_lixo_intercalado_e_suspeito():
    """RUE-5G20, 07/10/2026: 0–2% intercalado com o nível real de ~25% (saltos menores que 30 pontos)."""
    v, h = veiculo(arla=1.0), hist(arla_n=380.0, arla_total=385.0, arla_med=1.0, arla_saltos=0.0, arla_subidas=40.0, arla_quedas=40.0)
    assert [x["sinal"] for x in _suspeitos(v, h)] == ["arla"]
    assert "arla" not in chaves(_alertas_do_veiculo(v, h))


def test_temperatura_absurda_e_ignorada():
    """Acima de 150 °C é código de erro do equipamento, não leitura."""
    assert _alertas_do_veiculo(veiculo(temp=250.0), hist()) == []


# ---------------------------------------------------- plano preventivo

def item(**kw):
    return {"servico": "Troca de óleo", **kw}


def ultimo(dias_atras=0, km=100_000, horas=None):
    return {"data": (date.today() - timedelta(days=dias_atras)).isoformat(), "odometro_km": km, "horimetro_h": horas}


def test_sem_registro_de_servico():
    assert _situacao_item(item(km=20_000), None, 150_000)["situacao"] == "sem_registro"


def test_vencido_por_km():
    r = _situacao_item(item(km=20_000), ultimo(km=100_000), 121_000)
    assert r["situacao"] == "vencido" and r["falta_km"] == -1000


def test_vence_em_breve_por_km():
    r = _situacao_item(item(km=20_000), ultimo(km=100_000), 119_500)
    assert r["situacao"] == "vence_em_breve" and r["falta_km"] == 500


def test_vencido_por_dias():
    assert _situacao_item(item(dias=180), ultimo(dias_atras=200), 100_000)["situacao"] == "vencido"


def test_vencido_por_horas_de_motor():
    """Horímetro: o plano usa horas (o equipamento manda minutos; a conversão é feita antes)."""
    r = _situacao_item(item(horas=1000), ultimo(horas=5000), 100_000, horas_atual=6010)
    assert r["situacao"] == "vencido" and r["falta_horas"] == -10


def test_em_dia():
    r = _situacao_item(item(km=20_000, dias=180), ultimo(dias_atras=10, km=100_000), 105_000)
    assert r["situacao"] == "em_dia"


# ------------------------------------------------------------- manutenção por risco

from datetime import date as _d, timedelta as _td

from app.api.v1.endpoints.manutencao_risco import inclinacao, nota, tendencias


def serie(campo, valores):
    return [{"dia": _d(2026, 9, 22) + _td(days=i), campo: v} for i, v in enumerate(valores)]


def test_inclinacao():
    assert round(inclinacao([(0, 1), (1, 2), (2, 3)]), 3) == 1.0


def test_um_dia_atipico_nao_vira_tendencia_de_bateria():
    """PZO-7A16: 13,4 V no primeiro dia (carga de superfície) e o resto estável."""
    v = [13.4, 12.5, 12.6, 12.5, 12.5, 12.4, 12.5, 12.5, 12.4, 12.5]
    assert tendencias(serie("v_repouso", v)) == []


def test_bateria_24v_caindo_de_verdade():
    v = [25.6, 25.5, 25.5, 25.4, 25.2, 25.0, 24.9, 24.8, 24.7, 24.6]
    t = tendencias(serie("v_repouso", v))
    assert [x["chave"] for x in t] == ["bateria"]


def test_consumo_de_veiculo_leve_com_pouco_km_nao_alerta():
    dias = [{"dia": _d(2026, 9, 22) + _td(days=i), "km_l": k, "km_comb": 80, "litros": 80 / k}
            for i, k in enumerate([5.5, 5.4, 5.6, 5.5, 4.6, 4.5, 4.6, 4.5])]
    assert tendencias(dias) == []          # 320 km por metade: abaixo de 800


def test_nota_soma_os_motivos_e_limita_em_100():
    v = {"alertas": [{"nivel": "critico", "titulo": "Motor quente", "valor": "108 °C"}] * 4, "vencidos": 2, "vencendo": 0}
    pontos, motivos = nota(v, [], 1.0)
    assert pontos == 100 and motivos[0]["pontos"] == 30


def test_arla_oscilando_e_suspeito_e_nao_alerta():
    """RNY-4F94, 06/10/2026: 90 -> 6 -> 89 -> 0% em minutos; mediana 8% virava "ARLA no fim"."""
    v, h = veiculo(arla=74.0), hist(arla_n=500.0, arla_total=520.0, arla_med=8.0, arla_saltos=180.0)
    assert [s["sinal"] for s in _suspeitos(v, h)] == ["arla"]
    assert "arla" not in chaves(_alertas_do_veiculo(v, h))


def test_arla_abastecido_nao_e_suspeito():
    """Abastecer é um salto só, para cima."""
    v, h = veiculo(), hist(arla_n=500.0, arla_total=500.0, arla_med=60.0, arla_saltos=1.0)
    assert _suspeitos(v, h) == []


def test_abastecimento_em_etapas_nao_e_suspeito():
    """UAO-0G30, 07/10/2026: 55 → 88 → 99% em 2 min (abastecimento gravado em etapas)."""
    v, h = veiculo(arla=99.0), hist(arla_n=500.0, arla_total=500.0, arla_med=60.0, arla_saltos=1.0, arla_subidas=3.0, arla_quedas=0.0)
    assert _suspeitos(v, h) == []
