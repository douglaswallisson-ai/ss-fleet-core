# Manutenção — `manutencao.py` (`/manutencao`)

Telas: `push-it-on-over/src/screens/manutencao/ManutencaoReal.tsx` (Painel,
preventiva, corretiva/OS), `components/ss/frota/InspecaoVeiculo.tsx` (sinais do
motor com os limites), `screens/manutencao/RelatorioVeiculo.tsx`.

Decisões do PM: preventiva = o CLIENTE cadastra o plano (por tipo, modelo ou
veículo) partindo de modelos da SS; corretiva = nasce de ALERTA automático e
vira ORDEM DE SERVIÇO, acompanhada até fechar (também abre à mão).

## Fontes
- Odômetro: `dev_status.odom` (m); 0 e 100.000.000 inválidos → `can_total_odometer`. Nunca `tracked_unit.initial_odometer`.
- Horímetro: `can_engine_hourmeter` / `hourmeter` em **minutos** ÷ 60 = `horimetro_h`.
- Sinais: colunas `can_*` e `voltage` da última leitura.

## Limites dos alertas (`LIMITES`)
- Cummins L9 (boletim 5676573, 2024): arrefecimento normal 79–95 °C, máx. 107 °C; óleo mín. 69 kPa em marcha lenta / 207 kPa na nominal.
- **Óleo = 0 → sem sensor** (não alarma; eram 120 falsos alertas).
- Bateria/alternador: 24 V carrega 27–29 V; repouso 25,4 V cheia, 24,4 V 50%; 12 V = metade.
- SUPOSIÇÃO: outras marcas (MWM, Mercedes, Scania, Volvo) usam os mesmos limites até haver manual.

## Como o alerta é julgado (desde 05/10/2026)
Uma leitura só não abre alerta: óleo, tensão e ARLA usam a **mediana das
últimas 24 h** (`_historico_24h`, `dev_status_30`), com pelo menos
`MIN_LEITURAS = 10`. Temperatura continua pela última leitura.
- **Óleo**: só em **marcha lenta (600–899 rpm) com o motor quente (≥ 75 °C)**. Acima disso, nos VIRLOC 8, a leitura passa de 255 kPa e volta do zero (CECOTI e Quataí: 184–188 kPa em marcha lenta e 84–100 kPa entre 900 e 1.499 rpm); motor frio estoura até em marcha lenta.
- **Tensão**: alternador pela mediana com o motor ligado (< 26 V / 13 V); bateria pela mediana em repouso (< 24,4 V / 12,2 V) — **atenção**, não crítico. Abaixo de 5 V = sem leitura.
- **ARLA**: mediana das leituras acima de 0 (0 o tempo todo = sem sensor).

**Sinal suspeito** (`sinais_suspeitos`, aviso à parte, não abre alerta): óleo com o mesmo valor em todas as leituras com o motor ligado (CECOTI: 20 veículos em 44 kPa); óleo estourando a escala; ARLA no fim e cheio no mesmo dia (pula 0/1/2/100%); tensão < 5 V.
Revisão de 05/10/2026 (validador independente em 7 clientes, 1.288 veículos):
- **Óleo inconclusivo**: se o sensor chegou a ≥ 240 kPa (`OLEO_TETO`) nos últimos 7 dias, leitura baixa em marcha lenta não abre alerta — a escala de 1 byte estoura e volta do zero (RCA: o mesmo caminhão com ~30 kPa num dia e ~200 kPa no outro). Vira sinal suspeito. Mínimo de 10 leituras.
- **ARLA sem leitura válida**: maioria das leituras com 102% (código "sem informação", J1939 SPN 1761 0xFF × 0,4) vira sinal suspeito; o valor solto no meio (ex.: 5%) não é o nível. CECOTI: 12 VW quebraram juntos entre 12h40 e 12h44 de 30/09/2026 (mudança no equipamento); TDP-2E24 tinha 41% antes.
- **Tensão com folga de medição**: a leitura é da alimentação do rastreador. Alerta só com folga: alternador < 25,5 V (24 V) / 12,8 V (12 V) com o motor ligado; repouso < 24,0 V / 12,0 V.
- Lição: alerta só sai depois de olhar o histórico de 10 dias atrás de quebra (mudança brusca), não só a regra do dia.

## Planos e vencimento
- Item do plano: a cada X km, Y dias e/ou Z horas. Aviso antes: `AVISO_KM = 1000`, `AVISO_DIAS = 15`, `AVISO_HORAS = 50`.
- Situação: vencido, vence em breve, sem registro, em dia (`ORDEM_SIT`).
- Modelos sugeridos (`MODELOS_PLANO`), ex.: óleo a cada 1.000 h, filtro de combustível 1.500 h.
- OS: `STATUS_OS = aberta, em_andamento, aguardando_peca, concluida, cancelada`.

## Gravação provisória
`data/manutencao.sqlite`: planos, serviços feitos (com `horimetro_h`) e ordens.
Migração por `ALTER TABLE` quando entra coluna nova.
