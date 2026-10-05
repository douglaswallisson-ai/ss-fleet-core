# Cliente, acessos e modo embutido — `cliente.py`, `acessos.py`, `embed.py`, `auth.py /me`

## Módulos e logo (`/cliente`)
- `GET /modulos`: urbano = ônibus em linha nos últimos 15 dias (`con_telemetry.line_number`);
  fretamento = ≥ 5 ônibus/micro (categorias 12, 22) e não urbano. Frota é de todos.
  A SS corrige por cliente (`PUT /modulos`, `data/cliente.sqlite`).
  Conferido em 04/10/2026: Fênix urbano; VTR e Quataí fretamento; FERTRAN, RCA, Figueiredo só frota; JTP frota + urbano.
- `GET /logo`: logo da empresa (cadastro provisório) ou `account.imglogo` **só se a
  conta tiver um único grupo** (a 539 é compartilhada e tem o logo da SS).

## Acessos (`/acessos`, só SS)
- Fonte `mova.session` (entradas do sistema antigo). ~95% são integrações (robô
  Synapse a cada 15 s, Power BI) — separadas; pessoa = navegador de verdade.
- Cliente = grupo principal do usuário (mais linhas em `user_group_access`);
  > 20 grupos = "acesso a vários clientes". IP não vai para a tela.
- Uso de telas da plataforma nova: `POST /registro` → `data/acessos_paginas.sqlite`.

## Super admin
`is_super_admin` = `user_mova = 1` **ou** id em `SS_ADMIN_USER_IDS` (.env) —
paliativo porque o usuário do PM (17761) está com `user_mova = 0`. `/auth/me`
devolve `user_mova`; o front trata como super_admin (Console de gestão,
Auditoria, Acessos).

## Modo embutido (`/embed/entrar`)
Parceiro (ex.: Citatti) gera ticket HMAC-SHA256 válido por 60 s e de uso único;
o iframe abre `/embed?ticket=…&tela=/app/mapa`. Parceiros em `EMBED_PARCEIROS`
(JSON no .env): contas permitidas, origens, marca. Usuário SS não entra por
parceiro, salvo `permitir_usuarios_ss`. No embutido a tela Suporte não aparece.
