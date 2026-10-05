# Mapa, posições e POIs — `positions.py`, `mapa.py`, `pois.py`

Telas: `push-it-on-over/src/screens/MapaAoVivo.tsx` (+ `components/ss/mapa/*`).

- **Posições** (`/positions`): `dev_status` = última leitura de cada equipamento,
  uma linha por veículo. Sem sinal há mais de `MINUTOS_SEM_SINAL = 20` = "sem sinal".
  Não confundir com `/reports/history` (rastro histórico).
- **Camadas** (`/mapa/camadas`): cercas (`cerca`) e POIs (`poi`) na área visível,
  até `LIMITE = 3000`. Cerca tipo 1 circular (centro + raio), 2 e 3 polígono, 4 linha.
  Polígonos gravados com X = latitude; a resposta já entrega [lat, lng].
- **POIs do uso** (`/pois`) e **visitas** (`/pois/{id}/visits`): derivados de
  `con_telemetry` (início/fim de viagem num POI). Passagem de verdade (parando ou não)
  está em `relatorios_frota.passagem-poi`.
- Front: chave dos marcadores = `veiculoId-placa` (placas repetidas existem).
