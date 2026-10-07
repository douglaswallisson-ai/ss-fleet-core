# Como rodar o servidor (ss-fleet-core) num computador novo

Este é o **servidor da plataforma nova** da SS. As telas ficam no repositório
[`push-it-on-over`](https://github.com/douglaswallisson-ai/push-it-on-over), que tem o seu próprio `COMO-RODAR.md`.

> ⚠️ **Use este repositório, ramo `main`:** https://github.com/douglaswallisson-ai/ss-fleet-core
>
> O `ss-fleet-core` que existe no **CodeCommit da AWS** é o projeto **original, antigo**, sem as funcionalidades novas
> (CCO, contratos, estoque, rotas seguras, manutenção etc.).

## O que precisa estar instalado

- **Python 3.12** (testado com 3.12.10).
- **Redis.** No Windows, um Redis local (ex.: a pasta `redis-local` com o `redis-server.exe`) ou o Docker Desktop com `docker run -p 6379:6379 redis:7-alpine`.
- **Acesso ao banco** Aurora PostgreSQL (`mova2`). Peça ao TI um usuário **somente leitura**: a plataforma nova nunca grava no banco de produção.

## Passo a passo

### 1. Baixar o código
```bash
git clone https://github.com/douglaswallisson-ai/ss-fleet-core.git
```
```bash
cd ss-fleet-core
```

### 2. Criar o ambiente e instalar as bibliotecas
```bash
python -m venv .venv
```
```bash
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Configurar o `.env`
Copie o modelo e preencha. O `.env` **nunca** vai para o GitHub, porque tem senha.
```bash
copy .env.example .env
```

Valores que precisam estar certos:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://<usuário>:<senha>@<endereço do banco>:5432/mova2` (usuário somente leitura; o TI passa os três) |
| `REDIS_URL` | `redis://localhost:6379/0` |
| `SECRET_KEY` | qualquer texto longo e aleatório (assina o login da plataforma nova; não precisa ser igual ao de outro computador) |
| `CORS_ORIGINS` | `["http://localhost:8080"]` |
| `SS_ADMIN_USER_IDS` | ids dos usuários da SS que veem todos os clientes (ex.: `17761`) |

As demais variáveis (câmeras, motor de rotas, Zendesk) são opcionais. Sem elas, a funcionalidade fica desligada e a tela explica. Ver o vault "MVP Novo Sistema SS", nota *Configuração do servidor*.

### 4. Ligar o Redis
Precisa estar no ar **antes** do servidor.

### 5. Subir o servidor
```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```
Teste em http://localhost:8000/docs: deve abrir a lista de rotas.

### 6. Arquivos da pasta `data/`
A pasta `data/` fica fora do GitHub. Ela é o **armazenamento provisório** (contratos, estoque, CCO etc.) e se cria sozinha. Duas exceções:

| Arquivo | Para quê | Como conseguir |
|---|---|---|
| `data/pracas_pedagio.csv` | praças de pedágio da ANTT (dado público) | já vem no repositório |
| `data/estoque_base.json` | os 2.589 seriais da planilha de Operações (Estoque de equipamentos) | copiar do computador do PM. Sem ele, a tela de Estoque fica vazia |

Os contratos dos clientes existentes se criam sozinhos a partir do banco, na primeira vez que a tela de Contratos abre.

## Não use o Docker deste repositório

O `docker-compose.yml` e o `Dockerfile` vieram do projeto original. A imagem base fica num repositório privado da AWS (ECR) e só funciona com login na AWS. Para rodar no computador, use o passo a passo acima.

## Problemas comuns

| Sintoma | Causa |
|---|---|
| As telas mostram a faixa amarela "MODO DEMONSTRAÇÃO" e dados de exemplo | o `.env` das **telas** está sem `VITE_API_BASE=http://localhost:8000` |
| Erro `Error 22 connecting to localhost:6379` | o Redis está desligado |
| Tudo vazio depois do login | o usuário do banco não tem permissão de leitura nas tabelas `mova.*` |
| Telas antigas, sem CCO, Contratos ou Rotograma | o código veio do CodeCommit (projeto original) e não deste GitHub, ou o clone não está no ramo `main` |

## Regras que não se quebram
- **Banco de produção e AWS são só leitura.**
- **Não rodar os testes antigos** (`test_devices_api`, `test_vehicles_api`…): eles gravam no banco de produção.
- **Os testes das regras podem ser rodados sempre:**
  ```bash
  .venv\Scripts\python.exe -m pytest tests -q -k regras
  ```

Mais detalhes em `CLAUDE.md` e em `app/api/v1/endpoints/CLAUDE.md`.
