# Endpoints — índice das funcionalidades

Uma funcionalidade por arquivo. O docstring do topo de cada arquivo é a fonte
primária das regras (fonte, correções, SUPOSIÇÕES). A pasta `_docs/` tem uma
ficha por funcionalidade com regras, campos, tabelas e a tela que consome.
Ao criar ou mudar uma funcionalidade: atualizar o docstring **e** a ficha.

Padrão de código: ver `../../../../CLAUDE.md` (raiz do projeto), seção 2.

## Índice

| Arquivo | Prefixo | O que faz | Grava? | Ficha |
|---|---|---|---|---|
| `positions.py` | `/positions` | posição atual de cada veículo (mapa ao vivo) | não | `_docs/mapa-e-posicoes.md` |
| `mapa.py` | `/mapa` | camadas do mapa: cercas e POIs na área visível | não | `_docs/mapa-e-posicoes.md` |
| `tracking.py` | `/tracking` | ignição, paradas e retomadas de um veículo num dia | não | `_docs/tracking-e-timeline.md` |
| `timeline.py` | `/eventos/timeline` | linha do tempo de eventos por veículo/motorista | não | `_docs/tracking-e-timeline.md` |
| `events.py` | `/events` | alarmes e ocorrências com reconhecimento | `acknowledge` grava em produção — **não usar** | `_docs/eventos-e-video.md` |
| `video.py` | `/video` | câmeras e ocorrências DMS/ADAS | não | `_docs/eventos-e-video.md` |
| `cco.py` | `/cco` | Painel CCO: veículos (ícone por categoria), avisos de segurança/câmera/Monitor/manutenção/operação (parado ligado, área de risco, desvio de rota, parada fora do lugar) agrupados, Visto/Tratado, turno | provisório `data/cco.sqlite` (marcações) | docstring do módulo |
| `manutencao_risco.py` | `/manutencao-risco` | nota de risco com motivos, tendência (bateria, temperatura, consumo), causa raiz | não | docstring do módulo |
| `estoque.py` | `/estoque` | estoque de equipamentos: serial × placa (ao vivo) × contrato/aditivo; expedição, manutenção, devolução | provisório `data/estoque.sqlite` (base `data/estoque_base.json`) | `_docs/estoque.md` |
| `contratos.py` | `/contratos` | contrato de todo cliente; grupo novo só por contrato; clientes existentes pré-preenchidos e sem trava; nunca encerra sozinho | provisório `data/contratos.sqlite` | `_docs/contratos.md` |
| `cameras.py` | `/cameras` | vídeo ao vivo (JIMI JC450 em FLV, Hikvision G40 em HLS) via proxies, chaves no `.env` | não grava; abrir canal pede transmissão ao equipamento | `_docs/eventos-e-video.md` |
| `bi.py` | `/bi` | páginas do Power BI "Indicadores de Condução" | não | `_docs/gerencial-e-bi.md` |
| `gerencial.py` | `/gerencial` | série diária, ocioso, ROI, CO₂ | não | `_docs/gerencial-e-bi.md` |
| `driver_ranking.py` | `/driver-ranking` | pontuação dos motoristas (fórmula do BI) | não | `_docs/gerencial-e-bi.md` |
| `fleet_health.py` | `/fleet-health` | saúde da frota (cascata T11/DS-1511) | não | `_docs/gerencial-e-bi.md` |
| `indicators.py` | `/indicators` | CPK/IPK/MKBF (sem custo) | não | `_docs/gerencial-e-bi.md` |
| `relevo.py` | `/relevo` | elevação do trajeto (SRTM) × consumo | Redis | `_docs/gerencial-e-bi.md` |
| `ai_fleet.py` | `/ai-fleet` | painel do IA Ops Advisor (funções `fleet_mvp.*`) | não | `_docs/ia-ops-advisor.md` |
| `combustivel.py` | `/combustivel` | controle de combustível (módulo do TI) | SQLite `combustivel` | `_docs/combustivel.md` |
| `manutencao.py` | `/manutencao` | preventiva (planos) e corretiva (alertas → OS) | SQLite `manutencao` | `_docs/manutencao.md` |
| `jornada.py` | `/jornada` | Lei do Motorista, escala, espelho de ponto, identificação | SQLite `jornada` | `_docs/jornada.md` |
| `escala_viagem.py` | `/escala-viagem` | plano de viagem para a GR + conformidade | SQLite `escala_viagem` | `_docs/escala-e-roteirizacao.md` |
| `roteirizacao.py` | `/roteirizacao` | km, tempo com pausas legais, combustível, pedágio; desvio de áreas de risco pelo motor (`app/core/roteador.py`) e `/rotograma` | não | `_docs/escala-e-roteirizacao.md` |
| `areas_risco.py` | `/areas-risco` | áreas de risco (cercas com categoria 8/9, marcadas, desenhadas) e programação de viagens (veículo + rota + horário) para os avisos de desvio no CCO | provisório `data/rotas.sqlite` | `app/core/areas_risco.py` |
| `operacao.py` | `/operacao` | monitor de viagens (fretamento) e viagens produtivas (urbano) | não | `_docs/operacao-urbano-fretamento.md` |
| `sinotico.py` | `/sinotico` | painel sinótico do urbano (régua, colado/buraco) | não | `_docs/operacao-urbano-fretamento.md` |
| `bus_lines.py` | `/bus-lines` | linhas, tabelas e cumprimento | não | `_docs/operacao-urbano-fretamento.md` |
| `passageiros.py` | `/passageiros` | embarques por cartão e taxa de frequência | não | `_docs/passageiros.md` |
| `cadastros.py` | `/cadastros` | motor único dos 17 cadastros | SQLite `cadastros` | `_docs/cadastros.md` |
| `relatorios_frota.py` | `/relatorios-frota` | paradas, POI, cercas, distância, horímetro, SLA, configurações, odômetro travado | não | `_docs/relatorios-frota.md` |
| `reports.py`, `history_detailed.py` | `/reports` | relatórios pesados com cursor e CSV | não | `_docs/relatorios-cursor.md` |
| `pois.py` | `/pois` | POIs derivados do uso + visitas (início/fim de viagem) | não | `_docs/mapa-e-posicoes.md` |
| `cliente.py` | `/cliente` | módulos do cliente (urbano/fretamento) e logo | SQLite `cliente` | `_docs/cliente-e-acesso.md` |
| `acessos.py` | `/acessos` | uso da plataforma por pessoa (só SS) | SQLite `acessos_paginas` | `_docs/cliente-e-acesso.md` |
| `embed.py` | `/embed` | modo embutido (parceiro, ex.: Citatti) | não | `_docs/cliente-e-acesso.md` |
| `suporte.py` | `/suporte` | chamados Zendesk (desligado sem variáveis) | SQLite `suporte` | `_docs/suporte.md` |
| `auth.py` | `/auth` | login, refresh, `/me` | não | `_docs/crud-legado.md` |
| `vehicles.py`, `devices.py`, `drivers.py`, `groups.py`, `subgroups.py`, `tracked_unit_devices.py`, `vcms_unit_devices.py`, `api_key_endpoint.py` | vários | CRUD herdado do projeto original | **gravam em produção — não usar** | `_docs/crud-legado.md` |

## Decisões do PM que valem para o backend

- Cada cliente vê só o módulo do seu segmento (FERTRAN = Frota; VTR =
  Fretamento; Fênix = Urbano). Segmento calculado em `cliente.py`.
- Ferramentas compartilhadas (Escala de viagem, Roteirização, Contagem de
  passageiros) são separadas e conversam — não fundir.
- Rotas do fretamento vivem na Roteirização (a página Rotas saiu).
- Horímetro em minutos. Pedágio estimado (ANTT × eixos × R$ 8,00) até haver
  serviço de rota com pedágio.
- Financeiro não entra. Comando para equipamento fica bloqueado.
- Jornada pela telemetria é conferência, não ponto.
- Plausibilidade do combustível: descarta dia > 1.200 L ou > 100 L com < 0,5 km/l.
