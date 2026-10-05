# Gerencial e BI — `gerencial.py`, `bi.py`, `driver_ranking.py`, `fleet_health.py`, `indicators.py`, `relevo.py`

Telas: `push-it-on-over/src/screens/RelatoriosGerenciais.tsx` + `screens/gerencial/*`
(Gerencial), `Motoristas.tsx` e `AcompanhamentoMotorista.tsx` (ranking),
`CO2Real.tsx`, `Inicio.tsx` (cartões).

## Regras (vault `Regras-de-Negocio/`)
- **Km filtrado** = linhas com combustível entre 0 e 500.000 mL, exclusivo (indicadores-dashboard-start R1). **Média km/L** = km filtrado ÷ litros (R4). Combustível negativo → 0 (Power BI P2). Valores `*_estimated` não entram.
- `stop_engine_on` inclui o parado produtivo (R5/R6). `total_11` = 11 faixas do Dashboard Start; `faixas_13` = 13 do Power BI.
- **Pontuação dos motoristas** (Power BI "Indicadores de Condução 5.0", vault P6): Σ(% de cada faixa × peso) + aceleração/h × peso da freada (15) + freada/h × 15 + velocidade/h × 13 + embreagem/h × 14 + (horas × 18 + km × 18) ÷ meses, truncado em 2 casas. Erros do BI preservados de propósito (é o que o cliente vê). Denominador = soma das 13 faixas.
- **Saúde da frota**: vale a fórmula do agregador T11/DS-1511 (cascata: a primeira condição que bate classifica; saudável = nenhuma), não a do Power BI. Mínimos: 1 h e 1 km.
- **CO₂** = litros evitados × 3,21 kg (`FATOR_CO2_KG_L`); a SS certifica REDUÇÃO medida, nunca neutralidade.
- **ROI/payback** = contrato inteiro ÷ economia (`cliente_financeiro_vigencia`).
- **Faixas de relevo** (m de subida / 100 km): plano < 400, ondulado < 900, montanhoso < 1.500, serra acima. Elevação do SRTM (`app/core/relevo.py`), não do equipamento (só 39% grava altitude). Cálculo de dia passado vai para o Redis.
- **Indicadores** CPK/IPK/MKBF: sem tabela de custo, custo não é devolvido (melhor ausente que inventado).
- Faixa vermelha: valores reais, mas pequenos — mostrar com casas decimais.
- Gerencial abre em "Últimos 30 dias" até o dia 7 do mês (`periodoPadrao` no front).

## Fontes
`con_telemetry_day` (faixas por dia), `con_driver_h_km` (km, horas, litros, eventos por motorista/dia), `heatmap` (eventos com local; atraso ~1 dia), `con_stop_engine_on` (parado ligado), `weight_range` (metas e pesos).
