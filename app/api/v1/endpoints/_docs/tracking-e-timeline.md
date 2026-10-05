# Tracking e linha do tempo — `tracking.py`, `timeline.py`

Telas: `push-it-on-over/src/screens/Tracking.tsx` (Frota › Tracking; antes
"Percurso do dia") e `screens/eventos/TimelineEventos.tsx`.

- **Tracking** (`/tracking`): eventos derivados das posições de `dev_status_30`
  em SQL (ligou, parou, retomou, parado ligado) para um veículo num dia.
  Movimento a partir de `LIMIAR_MOVIMENTO_KMH = 3`; parada a partir de
  `PARADA_MINIMA_SEGUNDOS = 120`. Um veículo por dia; o multi-veículo por período
  é o relatório Paradas e Deslocamentos.
- **Timeline** (`/eventos/timeline`): eventos de `dev_status_30` + nome em
  `tracker_event`, viagens de `con_telemetry` como barras. Não usa `heatmap`
  (atraso). Marcas técnicas (`TECNICOS`: posição periódica, ignição, início/fim
  de viagem, online, sensor de chuva…) não viram evento. `CRITICOS` e `LEVES`
  definem a cor. Janela máxima 72 h. Um veículo/motorista por vez (feedback do PM).
- Heartbeats de veículo parado (tudo zerado) são escondidos nos Sinais do motor.
