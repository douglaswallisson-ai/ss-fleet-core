"""Regras do Painel CCO (endpoints/cco.py) — sem banco."""

from app.api.v1.endpoints.cco import cor_do_carro, gravidade_camera, tipo_icone


def test_cores_da_especificacao():
    crit, mod = {"gravidade": "critico"}, {"gravidade": "moderado"}
    assert cor_do_carro([mod, crit], True, 60) == "vermelho"
    assert cor_do_carro([mod], False, 0) == "amarelo"      # alerta ganha da cor de parado
    assert cor_do_carro([], True, 40) == "verde"
    assert cor_do_carro([], True, 0) == "cinza" and cor_do_carro([], False, 0) == "cinza"


def test_gravidade_de_camera_pela_tabela_aprovada():
    assert gravidade_camera("Fadiga ao Volante") == ("camera", "critico")
    assert gravidade_camera("Uso de Celular ao Volante") == ("camera", "critico")
    assert gravidade_camera("Risco de Colisão Frontal (FCW)") == ("camera", "critico")
    assert gravidade_camera("Motorista Fumando") == ("camera", "moderado")
    assert gravidade_camera("Distração ou Bocejo") == ("camera", "moderado")
    assert gravidade_camera("Câmera DMS Obstruída") == ("equipamento", "moderado")


def test_icone_por_categoria():
    assert tipo_icone(12) == tipo_icone(22) == "onibus"
    assert tipo_icone(15) == "van"
    assert tipo_icone(3) == tipo_icone(None) == "caminhao"


def test_excesso_e_aceleracao_so_criticos_a_partir_de_20():
    from app.api.v1.endpoints.cco import gravidade_evento
    assert gravidade_evento(7, 5) == "moderado" and gravidade_evento(7, 20) == "critico"
    assert gravidade_evento(153, 19) == "moderado" and gravidade_evento(153, 40) == "critico"
    assert gravidade_evento(9, 1) == "critico"       # freada brusca continua crítica
    assert gravidade_evento(359, 99) == "moderado"   # curva brusca não escala
