# app — estrutura

- `api/v1/endpoints/` — uma funcionalidade por arquivo (ver o `CLAUDE.md` de lá e `_docs/`).
- `api/v1/api.py` — registro dos routers e prefixos. Funcionalidade nova entra
  no import da linha 8 e num `include_router(..., prefix="/<nome>", tags=[...])`.
- `core/` — regras e infraestrutura compartilhadas (ver `core/CLAUDE.md`).
- `middleware/auth.py` — `require_permission(recurso, acao)`; as rotas novas usam
  `require_permission("reports", "read")`. O objeto `user` (`CurrentUser`) traz
  `user_id`, `email`, `permissions`, `is_super_admin` e `group_access`
  [(grupo, subgrupo)]. O `_grupo_ok` das rotas novas libera super admin e,
  para os demais, confere o grupo em `group_access`.
- `models/user.py` — `is_super_admin` = `user_mova = 1` ou id em `SS_ADMIN_USER_IDS`.
- `models/`, `schemas/`, `routers/admin/` — herdados do projeto original (ORM e
  Pydantic em inglês). As rotas novas não criam modelos ORM: leem com SQL
  (`text()`) e devolvem dicionários com chaves em português.
