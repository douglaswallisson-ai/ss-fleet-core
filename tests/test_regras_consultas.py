"""Regras de consulta às tabelas divididas por dia (app/core/camera.py, app/core/periodo.py) — sem banco."""

from datetime import date, datetime, timedelta

from app.core import periodo
from app.core.camera import montar_fonte


def test_camera_le_so_as_partes_do_periodo():
    existentes = {"vcms_history_p20261010", "vcms_history_p20261011", "vcms_history_p20991002"}
    f = montar_fonte(date(2026, 10, 10), date(2026, 10, 11), existentes, ["id", "local_time"])
    assert "vcms.vcms_history_p20261010" in f and "vcms.vcms_history_p20261011" in f
    assert "p20991002" not in f                        # parte futura não entra
    assert "FROM ONLY vcms.vcms_history" in f          # a mãe (vazia) continua, por segurança
    assert f.count("UNION ALL") == 2


def test_camera_dia_sem_parte_e_periodo_longo():
    f = montar_fonte(date(2026, 10, 12), date(2026, 10, 12), set(), ["id"])
    assert "UNION ALL" not in f and "ONLY vcms.vcms_history" in f
    assert montar_fonte(date(2025, 1, 1), date(2026, 10, 11), set(), ["id"]) == "vcms.vcms_history"


def test_limite_final_de_data():
    p = periodo.com_limite({"g": 1})
    assert p["g"] == 1 and datetime.now() < p["ate_limite"] <= datetime.now() + timedelta(hours=1, seconds=5)
    assert periodo.com_limite({"ate_limite": datetime(2026, 1, 1)})["ate_limite"] == datetime(2026, 1, 1)
