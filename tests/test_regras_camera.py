"""Classificação das ocorrências de câmera (app/core/camera.py) — sem banco."""

from app.core.camera import JOIN_TIPO, categoria_sql, severidade_video, tipo_video


def test_tipo_da_tela_pelo_nome_do_catalogo():
    assert tipo_video("Fadiga ao Volante") == "fadiga"
    assert tipo_video("Distração ou Bocejo") == "distracao"
    assert tipo_video("Olhos Fechados") == "olhos_fechados"
    assert tipo_video("Veículo Muito Próximo à Frente") == "proximidade_dianteira"
    assert tipo_video("Distância Insuficiente ao Veículo à Frente") == "proximidade_dianteira"
    assert tipo_video("Risco de Colisão Frontal (FCW)") == "risco_colisao"
    assert tipo_video("Frenagem Automática de Emergência (AEB-F)") == "colisao"
    # Sem equivalente na tela: chave a partir do nome (a tela mostra o nome).
    assert tipo_video("Sem Cinto de Segurança") == "sem_cinto_de_seguranca"


def test_severidade_segue_a_tabela_aprovada():
    assert severidade_video("Fadiga ao Volante") == "critical"
    assert severidade_video("Uso de Celular ao Volante") == "critical"
    assert severidade_video("Motorista Fumando") == "warning"
    assert severidade_video("Câmera DMS Obstruída") == "warning"
    assert severidade_video("Captura automática") == "info"


def test_nome_do_tipo_usa_modelo_e_source():
    # O id sozinho troca DMS por ADAS no mesmo modelo.
    assert "t.device_model_id = h.device_model_id" in JOIN_TIPO
    assert "t.source = h.alarm_source" in JOIN_TIPO
    sql = categoria_sql()
    assert "'equipamento'" in sql and "IN (65, 265, 101) THEN 'dms'" in sql and "IN (64, 264, 100) THEN 'adas'" in sql
