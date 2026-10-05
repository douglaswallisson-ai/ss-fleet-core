# Relatórios com cursor — `reports.py`, `history_detailed.py` (`/reports`)

Tela: `push-it-on-over/src/screens/RelatoriosOperacionais.tsx` (abas Consumo por
motorista, Faixas de RPM, Mapa de calor, Metas e pesos, Histórico de posições) e
`TelemetriaViagens.tsx`.

- Cada relatório tem `/cursor` (paginação por cursor, até `MAX_DAYS_CURSOR = 93`
  dias), `/export/estimate` e `/export/csv` (streaming, até `MAX_DAYS_EXPORT = 31`).
- Relatórios: `history` (posições), `history/detailed` (CAN aninhado),
  `driver-km-fuel-hours`, `telemetry` (`con_telemetry`, 113 campos por viagem),
  `rpm-band-time`, `heatmap`, `weight-range`.
- Filtrar sempre pela lista de veículos da empresa ativa — sem ela a consulta
  trazia todas as empresas do acesso (`useVeiculosDoRelatorio` no front).
- Herdado do projeto original (código em inglês); o padrão novo está em
  `relatorios_frota.py`.
