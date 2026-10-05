# Contagem de passageiros — `passageiros.py` (`/passageiros/contagem`)

Tela: `push-it-on-over/src/screens/passageiros/ContagemReal.tsx`
(Fretamento e Urbano › Contagem de passageiros; protótipo só em modo demonstração).

Regras do sistema antigo: `reportpassengerboardingcontroller` (Embarque e
Desembarque) e `reporttripoccupancycontroller` (Taxa de Frequência, que lá vem
de API externa).

- Embarque = `passenger_board` (evento 273 já ligado à viagem); passageiro pelo
  cartão (`passenger.cod`) do mesmo grupo; sem passageiro = "cartão sem cadastro".
- "Na lista" = existe `buss_line_shift_passenger` (status 1) do passageiro naquela tabela.
- Total por viagem = `con_status_buss_line.passenger_qtd` (bate com os embarques: VTR, 8.179 em 7 dias).
- Ocupação só quando o veículo tem capacidade (`max_passenger`, `unit_qtd_passenger` ou `tracked_unit.qtd_passenger`).
- Desembarque (274) quase não chega: só contagem, sem "a bordo".
- Taxa de frequência = embarques do passageiro ÷ viagens feitas da tabela no período (limitada a 100%).
  SUPOSIÇÃO: definição de "viagens" igual à do relatório antigo — confirmar com um passageiro da VTR.

Parâmetros: `group_id`, `inicio`, `fim` (≤ 31 dias). Devolve `resumo`, `por_dia`,
`por_hora`, `por_ponto` (30), `viagens` (500), `frequencia`, `cartoes_sem_cadastro` (50).
Cliente sem validador (ex.: Fênix) devolve tudo zerado e a tela explica.
