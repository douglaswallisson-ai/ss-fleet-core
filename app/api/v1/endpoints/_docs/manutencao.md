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
Resultado na CECOTI: de 17 alertas (8 críticos) para 8 alertas sem crítico + 47 sinais suspeitos.
SUPOSIÇÃO: RCA tem óleo coerente mas baixo em marcha lenta (20–64 kPa); o limite de 69 kPa é Cummins e o modelo do motor não está no cadastro — confirmar a marca antes de concluir.

## Planos e vencimento
- Item do plano: a cada X km, Y dias e/ou Z horas. Aviso antes: `AVISO_KM = 1000`, `AVISO_DIAS = 15`, `AVISO_HORAS = 50`.
- Situação: vencido, vence em breve, sem registro, em dia (`ORDEM_SIT`).
- Modelos sugeridos (`MODELOS_PLANO`), ex.: óleo a cada 1.000 h, filtro de combustível 1.500 h.
- OS: `STATUS_OS = aberta, em_andamento, aguardando_peca, concluida, cancelada`.

## Gravação provisória
`data/manutencao.sqlite`: planos, serviços feitos (com `horimetro_h`) e ordens.
Migração por `ALTER TABLE` quando entra coluna nova.
