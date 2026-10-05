# app/core — peças compartilhadas

| Arquivo | O que tem | Quem usa |
|---|---|---|
| `config.py` | `settings` (pydantic, lê o `.env`). Variáveis da plataforma nova: `EMBED_PARCEIROS` (JSON), `SS_ADMIN_USER_IDS` (ids separados por vírgula), `ROTEIRIZADOR_OSRM_URL` (vazio = estimativa) | todos |
| `database.py` | `AsyncSessionLocalReplica` / `get_db_read` (leitura — **use sempre**), `get_db` (escrita — não usar enquanto o banco for só leitura). Sem `DATABASE_REPLICA_URL` a leitura vai para o principal (aviso no log) | todos |
| `access_control.py`, `escopo.py` | grupos/subgrupos do usuário (`user_group_access`, cache Redis) e a cláusula SQL de escopo | rotas herdadas; as novas usam `_grupo_ok` local |
| `redis.py` | `get_cache` / `set_cache` / rate limit | relevo, acesso |
| `combustivel.py` | **regra única de plausibilidade do combustível** em SQL: `valido()`, `litros_ml()`, `km_com_combustivel_m()` — descarta dia > 1.200 L (`LITROS_MAX_DIA_ML`) ou > 100 L com < 0,5 km/L | gerencial, roteirização, combustível, ranking |
| `fuel_calc.py` | cópia das regras do módulo de combustível do TI: `ALERT_TYPES`, `DEFAULT_THRESHOLDS`, `compute_alerts`, `consumption_status`, `cycle_excluded_from_average`, `normalize_plate`, `levenshtein_distance`; acrescentado `CONSUMO_IMPOSSIVEL` (`KML_IMPOSSIVEL`) | `endpoints/combustivel.py` |
| `relevo.py` | elevação SRTM por ladrilho (`data/relevo/`), `acumular()` subida/descida | `endpoints/relevo.py` |
| `security.py`, `secure_session.py` | JWT e sessão | auth |

Regra: cálculo usado por mais de uma tela mora aqui, num lugar só. Se a mesma
conta aparecesse em dois endpoints com dois códigos, os números divergiriam.
Ao mudar uma regra daqui, procurar todos os usos (`grep -rn "combustivel\." app`).
