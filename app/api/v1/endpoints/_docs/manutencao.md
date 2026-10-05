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

## Planos e vencimento
- Item do plano: a cada X km, Y dias e/ou Z horas. Aviso antes: `AVISO_KM = 1000`, `AVISO_DIAS = 15`, `AVISO_HORAS = 50`.
- Situação: vencido, vence em breve, sem registro, em dia (`ORDEM_SIT`).
- Modelos sugeridos (`MODELOS_PLANO`), ex.: óleo a cada 1.000 h, filtro de combustível 1.500 h.
- OS: `STATUS_OS = aberta, em_andamento, aguardando_peca, concluida, cancelada`.

## Gravação provisória
`data/manutencao.sqlite`: planos, serviços feitos (com `horimetro_h`) e ordens.
Migração por `ALTER TABLE` quando entra coluna nova.
