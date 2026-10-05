# Escala de viagem e Roteirização — `escala_viagem.py`, `roteirizacao.py`

Telas: `push-it-on-over/src/screens/EscalaViagem.tsx` (menu em Frota, Urbano e
Fretamento) e `screens/roteirizacao/RoteirizacaoReal.tsx`.

**Decisão do PM**: são duas ferramentas separadas que conversam. A escala manda
a viagem para a roteirização (`?viagem=ID`); a roteirização salva rota para a
escala ("Salvar para a escala"). A carga também usa as duas. As rotas do
fretamento (`mova.route`) vivem na Roteirização.

## Escala de viagem (`/escala-viagem`)
Plano declarado à gerenciadora de risco + conformidade previsto × realizado.
A SS é a fonte de evidência, não a GR.
- Plano em `data/escala_viagem.sqlite` (rotas padrão, viagens, pontos autorizados, destinatários).
- Realizado: paradas de `con_stop` e último sinal de `dev_status`.
- Parada conta a partir de `PARADA_MIN_MIN = 10`; dentro de `RAIO_PADRAO_M = 300` de um ponto autorizado é conforme; fora é desvio.
- Janela: 2 h antes da saída prevista até a chegada (ou 12 h após a prevista).
- Meta de conformidade 95% (SUPOSIÇÃO). Alertas por e-mail (não implementado); integração com GR não existe.

## Roteirização (`POST /roteirizacao/calcular`)
Entrada: `group_id`, `pontos` (nome, lat, lng, parada_min), `unit_id`, `saida`,
`eixos`, `tarifa_eixo`, `kml`, `preco_litro`, `operacao`, e — para rota cadastrada —
`trajeto` [[lat, lng]…] e `rota_id`.
- **Caminho**: com `ROTEIRIZADOR_OSRM_URL`, malha viária real. Com `trajeto`, cada ponto é localizado no traçado (≤ 1,5 km) e o trecho mede o caminho real. Sem nada, linha reta × 1,3 (SUPOSIÇÃO).
- **Tempo**: OSRM; ou mediana das viagens da rota em 90 dias (`buss_line_shift_route_id`, ≥ 3 viagens); ou velocidade mediana das rotas do cliente (VTR 15 km/h com paradas); ou 60 km/h carga / 65 km/h passageiros.
- **Pausas** (Lei 13.103): CTB 67-C — 30 min a cada 5h30 (carga) / 4 h (passageiros); CLT 235-C — 11 h de descanso quando a direção passa de 10 h no dia.
- **Combustível**: km/L real do veículo (90 dias, `con_driver_h_km`), senão da frota, senão 2,5/3,0 (SUPOSIÇÃO). Preço: ANP, média dos estados (`precos_combustivel`).
- **Pedágio**: praças da ANTT (`data/pracas_pedagio.csv`, Latin-1, sem Free Flow, deduplicadas) a 1,5 km do caminho real ou 15 km da linha reta; valor = praças × eixos × tarifa (padrão R$ 8,00 — SUPOSIÇÃO aprovada como estimativa).
- Operação "passageiros" se o veículo é ônibus/micro (categorias 12, 22) ou se é rota de fretamento.
