# Combustível — `combustivel.py` (`/combustivel`) + `app/core/fuel_calc.py` + `app/core/combustivel.py`

Tela: `push-it-on-over/src/screens/combustivel/ControleCombustivel.tsx`
(menu Frota › Combustível; Cadastros › Combustível abre na aba Postos).

Origem: cópia fiel do módulo "Controle de Combustível V2" do time de TI
(ss-bi-start `/fuel` + `ss-fleet-core fuel_*.py` no CodeCommit), a pedido do PM.

## Fontes (leitura)
- `fuel_supply` (abastecimentos: CTA Smart, RDP Online, manual, planilha, migrados);
- `fuel_supply_telemetry` (km informado × rastreador: OK, DIVERGENTE, SEM_TELEMETRIA, SEM_KM_INFORMADO);
- `fuel_unit_profile` (tanque e faixa de km/L por veículo); `fuel_station`, `fuel_supplier`, `fuel_type`;
- `fuel_supply_staging` (transações de cartão que não viraram abastecimento = pendências).

## Regras
- **km/L por ciclo** de tanque cheio a tanque cheio, igual à view `v_fuel_supply_calc` (refeita em Python em `_ciclos` para incluir o que é lançado aqui).
- **Alertas** (`fuel_calc.ALERT_TYPES`, `compute_alerts`, `DEFAULT_THRESHOLDS`): duplicado, km regressivo, acima do tanque, consumo fora da faixa, etc. Acrescentado aqui: `CONSUMO_IMPOSSIVEL` (diesel fora de 0,3–15 km/L; Otto 0,3–30) — entra em `CYCLE_EXCLUDING_CODES`.
- **Média do período** sem os ciclos com erro. **Custo/km** pelo valor de cada ciclo (`_ciclo_valor`), não gasto total ÷ km válido (o módulo do TI fazia assim e dava R$ 36/km).
- **Pendências**: códigos de erro traduzidos em `_motivo()` (ex.: placa genérica sem unidade, placa sem rastreador).
- Plausibilidade compartilhada (`app/core/combustivel.py`): dia > 1.200 L ou > 100 L com < 0,5 km/L é descartado (`valido()`, `litros_ml()`, `km_com_combustivel_m()`).

## Gravação provisória (`data/combustivel.sqlite`)
Lançamento, correção, exclusão (com motivo), verificação de alerta, perfil do veículo, postos e pendências resolvidas/descartadas. IDs locais a partir de `ID_LOCAL = 900_000_000`. Correção recalcula a conferência com o rastreador **antes** de pegar a trava.

## Campos editáveis
Lançamento manual: todos (`EDITAVEIS_TODOS`). Vindo de integração (cartão): só km informado, tanque cheio, motorista e observação (`EDITAVEIS_INTEGRACAO`).

## Rotas
`catalogo`, `painel`, `sem-abastecimento`, `veiculo/{id}` (+ `km-sugerido`, `perfil`), `alertas`, `abastecimentos` (validar, criar, PUT, excluir, verificar, histórico), `postos`, `pendencias` (detalhe, resolver, descartar), `planilha` (importação).
