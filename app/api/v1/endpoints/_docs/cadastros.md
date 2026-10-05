# Cadastros — `cadastros.py` (`/cadastros`)

Telas: `push-it-on-over/src/screens/cadastros/` (`CadastroTela.tsx` + `config.tsx`).

## Como funciona (motor único)

Cada cadastro é um `Tipo(nome, sql, validar, unicos, pode_excluir, exige_motivo, so_ss_cria)`
registrado em `TIPOS`. Um cadastro novo = um SQL de leitura + uma função de
validação + (opcional) regras de unicidade e de exclusão + uma `CFG_*` no front.

- **Leitura**: o SQL do tipo, filtrado por `group_id` (`:g`), com cache de 120 s.
- **Gravação provisória**: `data/cadastros.sqlite`, tabela `registro`
  (tipo, id, group_id, origem_id, dados JSON, excluido, motivo, autor, em) e
  `historico` (cada create/update/delete com autor e motivo).
- **Sobreposição** (`_lista`): a linha do banco recebe por cima a edição
  provisória (id vira `e<origem_id>`); exclusão provisória esconde a linha;
  criações novas ganham id `n<n>` e `origem: "plataforma"`. Toda linha tem
  `provisorio` true/false — a tela mostra o selo.
- **Validação**: `validar(d, ctx)` levanta `Erro(campo, mensagem)` → 422
  `{"message", "field"}`; `ctx["avisos"]` devolve avisos sem bloquear.
- **Unicidade**: `unicos(d)` → lista `(campo, mensagem, chave)`; compara com a
  lista do grupo (login de usuário e dispositivo: com o sistema todo).

Rotas: `GET /{tipo}?group_id&busca`, `POST /{tipo}`, `PUT /{tipo}/{id}`,
`POST /{tipo}/{id}/excluir` (com `motivo`), `GET /{tipo}/{id}/historico`,
`GET /opcoes/todas` (listas dos selects, cache 10 min).

## Tipos, campos e regras (do sistema atual — vault `Telas/`)

| Tipo | Tabela | Campos principais | Regras |
|---|---|---|---|
| `empresa` | `group` | nome, razão social, CNPJ, velocidade máx., endereço, contatos, pró-rata, código do cliente, logo | só a SS cria; CNPJ 14 dígitos; velocidade 0–200; logo PNG/JPG/SVG/WEBP ≤ 500 KB (data URL) |
| `subgrupo` (Unidade) | `subgroup` | nome, empresa, CNPJ, endereço, tolerância antes/depois, cor, código | nome único; não exclui com veículos/motoristas/usuários dentro |
| `garagem` | só provisório | nome, unidade, endereço, vagas, responsável, localização+raio | não existe no sistema atual |
| `veiculo` | `tracked_unit` | placa, carreta, descrição, odômetro/horímetro inicial, tipo, categoria, unidade, velocidade máx., passageiros, modelo, ano, condutor fixo, fuso, média de consumo, horário de verão | placa `ABC1234`/`ABC1D23` (hífen aceito); exclusão exige motivo da lista `tracked_unit_removed_reason`; média de consumo agora é gravada (no antigo não era) |
| `dispositivo` | `device` | identificador, modelo, fabricante, IMEI, ICCID, linha, operadora, tipo, produtos | identificador alfanumérico ≤ 50; IMEI 15 dígitos (hífen corrigido) + aviso de Luhn; linha `(99) 99999-9999`; tipo ANALOGICO/CAN; únicos no sistema todo entre ativos; não exclui se vinculado |
| `vinculo` | `tracked_unit_device` | veículo, dispositivo, principal | um equipamento ativo por veículo |
| `usuario` | `users` | nome, login, e-mail, perfil (do master), unidades, horário de acesso, usuário SS | login único no sistema todo; ≥ 1 unidade; só SS cria usuário SS |
| `alarme` | `alarm*` | nome, unidade, nível, regras ("disparar quando…"), e-mails, descrição | ≥ 1 regra; 1 evento e 1 evento de câmera no máximo; e-mails separados por `;` ≤ 500 |
| `cerca` | `cerca` | nome, tipo (1 circular, 2 retângulo, 3 polígono, 4 linha), pontos, raio, espessura, velocidade, categoria, embarcada | nome sem acento/especial; máx. 31 pontos (32 em linha; 50 embarcada); circular não embarca; espessura 15/25/50 |
| `poi` | `poi` | nome, localização, raio, categoria, cor, ícone, descrição | nome sem aspas e `|`; raio 10–5.000 m |
| `ponto_parada` | `poi` (papel) | nome, papel (parada / ponto de controle), localização | PC é onde a viagem abre e fecha |
| `linha` | `buss_line` | nome, descrição, unidade, modalidade, km, duração, circular, itinerário (pontos) | km 0–2.000; duração < 24 h |
| `passageiro` | `passenger` | nome, CPF, matrícula, cartão RFID, assento, centro de custo, unidade, empresa, gerência, turno, entrada/saída, endereço, inatividade | centro de custo obrigatório; CPF 11 dígitos e único; cartão único; inatividade com fim ≥ início |
| `centro_custo` | `cost_center` | nome, código de integração | nome único; não exclui com passageiros ou linhas |
| `turno` | `bus_line_shift_shift` | nome | nome único |
| `grupo_linhas` | `line_group` | nome, unidade, linhas | ≥ 1 linha |
| `rota` | `route` | nome, cor, descrição, velocidade, centro de custo, traçado (só leitura) | traçado com eixos trocados no banco — o SQL desvira (`ST_FlipCoordinates`); editada na Roteirização |
| `layout_assentos` | `bls_seat_layout` | nome, assentos, centro de custo, descrição, imagem | 1–100 assentos; imagem ≤ 1 MB |

Fora de propósito: validação de pedido de assento (último pedido em 2021).

## Para criar um tipo novo

1. Ler o controller antigo (`<nome>controller-class.php`) e a nota do vault.
2. `SQL_<TIPO>` com aliases em português (`AS nome`, `AS placa`…).
3. `_v_<tipo>` com as mesmas mensagens do sistema antigo.
4. Registrar em `TIPOS`; listas de apoio em `opcoes()`.
5. Front: `TipoCadastro` em `lib/cadastros-api.ts`, `CFG_<TIPO>` em
   `screens/cadastros/config.tsx`, rota em `routes/`, item no menu.
