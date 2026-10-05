# ss-fleet-core — guia para quem (pessoa ou IA) for mexer aqui

Backend da plataforma nova da SS Telemática (FastAPI + SQLAlchemy assíncrono,
PostgreSQL `mova.*` em produção, Redis). O front é o repositório
`push-it-on-over`. Este arquivo explica **como o trabalho foi feito** para que
outra IA continue no mesmo padrão. Cada pasta tem o seu `CLAUDE.md` com as
regras e os campos daquela parte.

## 1. Regras que não se quebram

1. **Banco de produção e AWS são SÓ LEITURA.** Nada de INSERT/UPDATE/DELETE,
   procedure, comando para equipamento ou mudança de configuração na AWS.
   Tudo o que a plataforma "grava" vai para um **armazenamento provisório**
   (SQLite em `data/`, fora do git) e aparece junto com o dado real marcado
   como provisório. Onde gravar em produção é decisão da engenharia.
2. **O vault manda.** Antes de implementar qualquer cálculo, ler a regra no
   vault Obsidian (`OneDrive/Obsidian/Produtos SS/Sistema SS`): `Regras-de-Negocio/`,
   `Telas/`, `Relatorios/`. Quando a regra do vault contradiz o que parece
   lógico, o vault está certo. Se o vault aponta um erro do sistema antigo,
   corrigir aqui e **anotar no docstring** o que mudou e por quê.
3. **Sistema antigo como referência.** O `plataforma_web` (PHP) está no
   CodeCommit e é lido só com `aws codecommit get-file` (leitura). Antes de criar
   uma tela ou relatório, procurar o controller equivalente
   (`dashboard-api/controller/<nome>controller-class.php`) e copiar as regras,
   os campos e os textos. Não duplicar o que a plataforma já tem.
4. **Nunca inventar número.** Sem dado, a resposta diz que não há dado. Toda
   hipótese não confirmada leva `SUPOSIÇÃO:` no comentário, com como
   confirmar. Valor impossível é descartado e listado, nunca somado.
5. **Segredos só em variável de ambiente** (`.env`, fora do git). Nunca copiar
   token, senha ou chave que apareça no código antigo.
6. **Commit só local**, a não ser que o PM peça para subir. Mensagem em
   português, explicando o porquê, terminando com
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## 2. Como um endpoint novo é feito (o padrão)

Arquivo em `app/api/v1/endpoints/<funcionalidade>.py`, registrado em
`app/api/v1/api.py` com `prefix="/<funcionalidade>"`.

```python
"""
<Nome da funcionalidade> — o que é, em uma frase.

Fonte e regras (de onde saiu cada regra: vault, controller antigo, lei, manual):
- tabela `mova.x`: o que é e as armadilhas (unidade, eixo trocado, nulos);
- regra 1 ... (vault: Pasta/Nota);
- Correção: o sistema antigo fazia X; aqui Y, porque Z.
SUPOSIÇÃO: ... Confirmar com ...
"""
router = APIRouter()

def _grupo_ok(user, group_id):          # acesso: super admin (SS) vê tudo; os demais só os grupos de user.group_access
    ...

@router.get("/painel")
async def painel(group_id: int = Query(...), inicio: date = Query(...), fim: date = Query(...),
                 user=Depends(require_permission("reports", "read"))):
    _grupo_ok(user, group_id)
    # período limitado (31 dias, 7 para consultas por posição), cache em memória de 5 min
    ...
```

Padrões repetidos em todos os módulos:

- **Escopo**: toda rota recebe `group_id` (o cliente é o GRUPO, não a conta —
  quase todos estão na conta 539) e chama `_grupo_ok`.
- **Leitura**: `AsyncSessionLocalReplica` + `text(sql)` com parâmetros
  nomeados. `SET LOCAL statement_timeout` nas consultas pesadas.
- **Tabelas grandes** (`dev_status_30`, `heatmap`, `con_stop`): filtrar SEMPRE
  pela lista de veículos do grupo (`unit_id IN (SELECT id FROM mova.tracked_unit
  WHERE group_id = :g)`) e por tempo — os índices são (unit_id, local_time).
- **Limites**: período máximo (`MAX_DIAS`), linhas máximas (`LINHAS_MAX`) com
  aviso `cortado: true` para a tela pedir filtro.
- **Cache**: dicionário `_CACHE[(chave)] = (time.time(), resultado)` com
  `CACHE_S` (60–600 s). Dia passado que não muda pode ir para o Redis.
- **Plausibilidade**: constantes nomeadas no topo (`KMH_SALTO = 200`,
  `KM_DIA_MAX = 2000`, `LITROS_MAX_DIA_ML`…), com a fonte do número.
- **Armazenamento provisório**: `ARQUIVO = .../data/<modulo>.sqlite`,
  `_trava = threading.Lock()`, `_con()` cria as tabelas. Nunca segurar a trava
  durante um `await`.
- **Erros para a tela**: `HTTPException(422, {"message": "...", "field": "campo"})`
  com texto em português simples (a tela mostra direto).
- **Datas**: `local_time` já é hora de Brasília. Devolver ISO (`isoformat()`).

## 3. Armadilhas do banco já descobertas (não redescobrir)

| Onde | Armadilha |
|---|---|
| `con_stop.total_km` | está em **metros** apesar do nome |
| `dev_status*.odom`, `odom_total` | metros; 0 e 100.000.000 são inválidos; usar só `odom_quality_flag` 'ok' ou vazio |
| `hourmeter`, `can_engine_hourmeter` | **minutos**; leituras soltas gigantes — somar só avanço plausível entre leituras |
| `cerca` e `route` (geometria) | gravadas com **X = latitude** (eixos trocados) |
| eventos 60/61 (cerca) | a saída (61) chega sem `area_id` |
| `con_status_buss_line` | viagem de linha/fretamento; `buss_line_shift_route_id` liga à `route`; `passenger_qtd` = embarques |
| `passenger_board` | só embarque (273); desembarque (274) quase não chega |
| `heatmap` | ~269 mi linhas, atraso de ~1 dia; índice (unit_id, tracker_event_id, local_time) |
| pressão do óleo = 0 | sem sensor, não alarme |
| heartbeats parados | posições de veículo desligado com tudo zerado — não são leitura |
| `account.imglogo` | conta compartilhada (539) — logo só se a conta tiver um grupo |

## 4. Como testar

- `uvicorn app.main:app --port 8000` (Redis local precisa estar no ar).
- Testar com clientes reais de cada segmento: **FERTRAN 13956** (carga),
  **VTR 15686** (fretamento), **Consórcio Fênix 14330** (urbano), JTP 14828
  (horímetro), RCA 15092 (combustível).
- Conferir número com o banco por SQL de leitura antes de dizer que está certo,
  e comparar com o relatório antigo quando houver.
- Erro de import: `python -c "import app.api.v1.endpoints.<modulo>"`.

## 5. Mapa das pastas

| Pasta | O que tem | Doc |
|---|---|---|
| `app/api/v1/endpoints/` | uma funcionalidade por arquivo | `CLAUDE.md` + `_docs/<funcionalidade>.md` |
| `app/core/` | regras compartilhadas (combustível, relevo, config, banco) | `CLAUDE.md` |
| `app/middleware/`, `app/models/`, `app/schemas/` | autenticação, permissões, modelos ORM | `CLAUDE.md` em `app/` |
| `data/` (fora do git) | SQLite provisórios + `pracas_pedagio.csv` (ANTT, Latin-1) | — |

Decisões de produto do PM que afetam o backend estão resumidas em
`app/api/v1/endpoints/CLAUDE.md` (seção "Decisões").
