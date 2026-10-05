# Relatórios de frota — `relatorios_frota.py` (`/relatorios-frota`)

Tela: `push-it-on-over/src/screens/relatorios/RelatoriosFrota.tsx`, aberta pela
central de Relatórios (`embutido: "frota"`, `abaFrota`). Todos só leitura,
cache de 5 min, `LINHAS_MAX = 5000` com `cortado: true`.

| Rota | Relatório antigo (usuários) | Fonte | Regra | Correção feita aqui |
|---|---|---|---|---|
| `paradas-deslocamentos` | reportstopandtrip (406) + consolidado | `con_stop` | `move_stop = 0` parada, ≠ 0 deslocamento; `total_km` em metros | média > 200 km/h = salto de odômetro: marcado e fora das somas |
| `paradas-poi` | reportstoppoi (183) | `con_stop` + `poi` | parada que termina num POI; resumo por ponto | — |
| `passagem-poi` | reportpasspoi (126) | `dev_status_30` (poi_id, poi_distance ≤ X m) | período ≤ 7 dias | posições seguidas (até 10 min) viram uma passagem com entrada, saída e permanência |
| `cercas` | reportcerca (76) | `dev_status_30` eventos 60/61 | entrada pareada com a saída seguinte do veículo | o antigo só pareava a 1ª entrada/saída do período; a saída chega sem `area_id` |
| `distancia-horimetro` | reportkmtotal (178), reportkm (118), reporthourmetertotal (33) | `dev_status_30` | km = maior − menor `odom_total` com ignição, só `odom_quality_flag` ok/vazia; horas = soma do avanço do horímetro (CAN, senão equipamento) que cabe no tempo entre leituras + 5 min | dia > 2.000 km ou > 24 h descartado e listado |
| `sla-paradas` | reportslastops (86, API externa no antigo) | `buss_line_shift_stops` × `con_status_buss_line` | atraso = realizado − (dia da viagem + programado) | SUPOSIÇÃO: "no horário" = ±5 min (`TOLERANCIA_MIN`) |
| `configuracoes` | reportconfcar (159) | `device_config` do equipamento principal | uma coluna por chave; principais primeiro (`CHAVES_PRINCIPAIS`) | — |
| `odometro-travado` | Painel de Calibração, aba Odômetro Travado (DS-1533) | `dev_status_30` 24 h + `dev_status_raw` + `odometer_stall_fix_log` | ≥ 30 leituras, valor único, todas ≠ ok, vel. máx. > 20; travado em 0 só se já teve > 0 em 30 dias; corrigível = 5 leituras brutas crescentes, variando, acima do travado | a correção (procedure no banco principal) não é feita aqui |
| `veiculos` | — | `tracked_unit` | lista para o filtro | — |

Ficaram de fora por já existirem na plataforma: BDV Eventos (= Eventos +
Contagem por viagem) e Espelho Dinâmico (= Gerencial › Evolução). Financeiro:
decisão do PM.
