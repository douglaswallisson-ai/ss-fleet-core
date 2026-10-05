# CRUD herdado — `vehicles.py`, `devices.py`, `drivers.py`, `groups.py`, `subgroups.py`, `tracked_unit_devices.py`, `vcms_unit_devices.py`, `api_key_endpoint.py`, `auth.py`

Vieram do projeto original (código e docstrings em inglês, ORM em `app/models/`).

⚠️ **POST/PUT/DELETE destes arquivos gravam direto no banco de produção**
(`get_db` + `commit`). Enquanto o banco for só leitura, a plataforma nova usa o
motor de `cadastros.py` (gravação provisória) e só os GETs daqui
(ex.: `/vehicles` para listas). Não criar telas que chamem a escrita destes.

Regras que estão aqui e valem para quem ler os dados:
- motorista: exclusão lógica (`status = -1`); login, CPF, e-mail e matrícula únicos por grupo; CNH única no sistema todo (11 dígitos), senha do app em SHA1;
- vínculo veículo ↔ equipamento: um ativo por veículo; `device_primary` 1 quando ativo; data de vínculo < data de liberação;
- API keys: cada usuário gerencia as próprias; ver/revogar de outros exige `admin.manage`.

Autenticação (`auth.py`): `/login`, `/refresh`, `/logout`, `/sso-handoff`, `/me`
(permissões, grupos e `user_mova`). Dependência de permissão nas rotas novas:
`require_permission("reports", "read")`.
