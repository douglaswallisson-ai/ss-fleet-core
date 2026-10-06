# Estoque de equipamentos — `estoque.py`

Tela: `push-it-on-over/src/screens/estoque/EstoqueEquipamentos.tsx` (Console › Estoque de equipamentos).
Regra: especificação "MVP de Controle de Estoque de Equipamentos" (Luiz Barreto, Operações, v0.3, 06/10/2026).

- Base: 2.589 seriais da planilha "Equipamentos em campo" (`data/estoque_base.json`, extraído do protótipo).
- Placa, grupo e leitura ao vivo do banco: `tracked_unit_device` (status 1, veículo ativo) e `vcms_unit_device` (câmeras).
- Desinstalado = histórico do banco (vínculo com `release_date`) ou vínculo aberto em veículo desativado.
- Chave = identificador normalizado + família do modelo (seção 5.4); serial longo de câmera casa só pelo número.
- Status: manual (manutenção, devolução) > Ativo > Em estoque. Matriz = "Estoque SS (matriz)" depois da devolução.
- Alertas: M, C, S, T da especificação + **P** (placa ativa que nunca transmitiu) e **D** (ligado a veículo desativado).
- Contrato: cliente da planilha → grupo (maioria das placas; senão nome; ou à mão em `/estoque/cliente-grupo`) → contrato do
  grupo. Expedição escolhe contrato e, opcionalmente, aditivo (o serial mostra `CT-00042-AD01`).
- Bloqueios da expedição: contrato encerrado; sem saldo; acima do saldo (pede aditivo). Saldo por tipo (rastreador × câmera)
  contra os veículos contratados (SUPOSIÇÃO, confirmar com Operações). Seriais que já estavam em campo nunca bloqueiam.

Conferido em 06/10/2026 contra o protótipo: +110 Ativos estão em placa ativa que nunca enviou posição (o Monitor de Unidades
só lista placa que já transmitiu) — aqui contam como Ativo com alerta P. Testes: `tests/test_regras_estoque.py`.
