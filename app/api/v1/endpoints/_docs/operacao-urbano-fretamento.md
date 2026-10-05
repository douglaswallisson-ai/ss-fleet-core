# Operação de linhas — `operacao.py`, `sinotico.py`, `bus_lines.py`

Telas: `push-it-on-over/src/screens/OperacaoLinhas.tsx` (Fretamento › Viagens e
Urbano › Gestão de viagens) e `screens/operacao/SinoticoUrbano.tsx` (Urbano ›
Painel sinótico).

## Monitor de viagens — fretamento (`GET /operacao/monitor`)
Horários do dia (`buss_line_shift`) × viagens executadas (`con_status_buss_line`).
Regras do Monitor de Viagens antigo (vault `Fretamento/tripmonitor`): tolerância
7 min depois / 5 min antes; não iniciada passado o horário + 10 min = atrasada.
Correção: dia passado sem viagem = "não realizada", não "não iniciada".

## Viagens produtivas — urbano (`GET /operacao/produtivas`)
`con_telemetry` com `trip_status = true`, `line_number`, `trip_direction`
(0 ida, 1 volta), `trip_number` (regra do `reporttripproduction`). Linha ligada a
`buss_line` quando o nome é numérico e igual ao número da linha.

## Painel sinótico (`/sinotico`)
- Régua de cada linha e sentido = trajeto real (`dev_status_30`) de uma viagem completa recente; ônibus encaixados pela posição atual (`dev_status`); a mais de 300 m = fora da rota.
- Ônibus "na linha": trecho mais recente nos últimos 40 min é desta linha.
- Intervalo até o da frente = distância na régua ÷ velocidade média da viagem de referência.
- Critério TCQSM (TCRP 165, cap. 5): **colado** < 50% do intervalo médio, **buraco** > 150%. Nível de serviço pelo Cvh: A ≤ 0,21, B ≤ 0,30, C ≤ 0,39, D ≤ 0,52, E ≤ 0,74, F acima.
- SUPOSIÇÃO: sem tabela horária, o intervalo médio atual faz o papel do programado.

## Linhas (`/bus-lines`)
`buss_line`, `buss_line_shift`, `buss_line_shift_stops`, `busline_schedule`.
Convenção `_exec`: `unit_id` programado, `unit_id_exec` realizado.

## Segmento do cliente
Urbano = ônibus rodando em linha nos últimos 15 dias; Fretamento = ≥ 5
ônibus/micro sem linha urbana (`cliente.py`).
