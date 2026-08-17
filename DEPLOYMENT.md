# 🚀 Deployment Guide

Guia completo para deployment automatizado do Fleet Management Platform via CodeCommit + Lambda.

---

## 📋 Visão Geral

O sistema de deployment automatizado utiliza:

1. **CodeCommit**: Repositório Git na AWS
2. **Lambda**: Função serverless que executa deployment
3. **Secrets Manager**: Armazenamento seguro da chave SSH
4. **EC2**: Servidor de produção (Ubuntu)

### Fluxo de Deployment

```
Developer Push → CodeCommit (main branch)
                      ↓
                 Lambda Trigger
                      ↓
              SSH to EC2 Server
                      ↓
              Execute deploy.sh
                      ↓
    Git Pull → Docker Build → Docker Up → Health Check
```

---

## 🔧 Pré-requisitos

### No seu ambiente local:
- AWS CLI configurado (`aws configure`)
- Credenciais AWS (obter do AWS Secrets Manager ou do administrador):
  - Access Key ID: `<obter-do-secrets-manager>`
  - Secret Access Key: `<obter-do-secrets-manager>`
- Python 3.11+
- pip instalado

> ⚠️ **SEGURANÇA**: Nunca commite credenciais AWS no repositório. Use AWS Secrets Manager ou variáveis de ambiente.

### No servidor EC2:
- Docker e Docker Compose instalados ✅
- Usuário: `ubuntu`
- Path de instalação: `/home/ubuntu/ss-fleet-core`
- Outras aplicações rodando:
  - Docker de monitoramento de vídeo
  - Gateway de comunicação Virloc 8 e JMAK

---

## 🏗️ Configuração Inicial (One-Time Setup)

### Passo 1: Configurar AWS CLI

```bash
# Configure AWS CLI com as credenciais
# Obtenha as credenciais do AWS Secrets Manager ou administrador
aws configure

# Será solicitado:
# AWS Access Key ID: <sua-access-key-id>
# AWS Secret Access Key: <sua-secret-access-key>
# Default region name: us-east-1
# Default output format: json
```

> ⚠️ **IMPORTANTE**: Obtenha as credenciais do administrador ou do AWS Secrets Manager. Nunca use credenciais hardcoded na documentação.

### Passo 2: Executar Setup Automatizado da AWS

Este script irá:
- Armazenar chave SSH no Secrets Manager
- Criar IAM Role para Lambda
- Fazer deploy da função Lambda
- Configurar trigger no CodeCommit

```bash
# Garantir que o script é executável
chmod +x scripts/aws_setup.sh

# Executar setup
./scripts/aws_setup.sh
```

**Output esperado:**
```
[timestamp] AWS CLI configured successfully
[timestamp] Step 1: Storing SSH key in AWS Secrets Manager...
[timestamp] SSH key stored in Secrets Manager successfully
[timestamp] Step 2: Creating IAM Role for Lambda...
[timestamp] IAM Role configured successfully
[timestamp] Step 3: Packaging Lambda function...
[timestamp] Lambda function deployed successfully
[timestamp] Step 4: Configuring CodeCommit trigger...
[timestamp] CodeCommit trigger configured successfully
=========================================
AWS Deployment Setup Complete!
=========================================
Secret Name: fleet-ssh-key
IAM Role: fleet-lambda-deploy-role
Lambda Function: fleet-codecommit-deploy-trigger
CodeCommit Repo: ss-fleet-core
Trigger: Enabled on 'main' branch
=========================================
```

### Passo 3: Configuração Manual no Servidor EC2

**Conectar ao servidor:**
```bash
ssh -i ss-gateway-jimi.pem ubuntu@34.236.90.56
```

**Clonar repositório (primeira vez):**
```bash
cd /home/ubuntu
git clone https://git-codecommit.us-east-1.amazonaws.com/v1/repos/ss-fleet-core
cd ss-fleet-core
```

**Criar arquivo .env:**
```bash
cp .env.example .env
nano .env
```

**Configurar variáveis de ambiente:**
```env
# Application
ENVIRONMENT=production
DEBUG=false
SECRET_KEY=<gerar-chave-segura-256-bits>

# Database (Aurora PostgreSQL)
DATABASE_URL=postgresql+asyncpg://user:password@aurora-endpoint:5432/database

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_CACHE_TTL=3600

# Security
CORS_ORIGINS=["https://fleet.example.com"]
JWT_SECRET_KEY=<gerar-chave-segura-256-bits>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# Monitoring (opcional)
SENTRY_DSN=
```

**Gerar chaves seguras:**
```bash
# Gerar SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"

# Gerar JWT_SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**Tornar deploy.sh executável:**
```bash
chmod +x scripts/deploy.sh
```

**Testar deployment manual:**
```bash
bash scripts/deploy.sh
```

**Output esperado:**
```
[timestamp] =========================================
[timestamp] Starting Fleet Management Platform Deploy
[timestamp] =========================================
[timestamp] Checking port availability...
[timestamp] Port 8000 (API) is available
[timestamp] Port 6379 (Redis) is available
[timestamp] Port 9090 (Prometheus) is available
[timestamp] Pulling latest code from CodeCommit...
[timestamp] .env file found
[timestamp] Stopping existing containers...
[timestamp] Building Docker images...
[timestamp] Starting containers...
[timestamp] Waiting for API to be ready...
[timestamp] API health check passed
[timestamp] =========================================
[timestamp] Deployment completed successfully!
[timestamp] API Port: 8000
[timestamp] Redis Port: 6379
[timestamp] Prometheus Port: 9090
[timestamp] =========================================
```

---

## 🔄 Deployment Automático

### Como funciona

Após a configuração inicial, todo push para a branch `main` no CodeCommit irá:

1. Acionar automaticamente a função Lambda
2. Lambda conecta via SSH ao EC2 (IP privado: 172.31.85.75)
3. Executa `scripts/deploy.sh` no servidor
4. Script verifica disponibilidade de portas
5. Faz git pull do código mais recente
6. Para containers existentes gracefully
7. Rebuilda imagens Docker
8. Inicia novos containers
9. Executa health check
10. Se falhar, faz rollback automático

### Fazer Deploy

```bash
# 1. Fazer alterações no código
git add .
git commit -m "feat: nova funcionalidade"

# 2. Push para main (aciona deployment automático)
git push origin main

# 3. Monitorar logs da Lambda no CloudWatch
# AWS Console → Lambda → fleet-codecommit-deploy-trigger → Monitor → View logs
```

### Verificar Status do Deployment

**Via CloudWatch Logs:**
```
AWS Console → CloudWatch → Log groups → /aws/lambda/fleet-codecommit-deploy-trigger
```

**Via SSH no servidor:**
```bash
ssh -i ss-gateway-jimi.pem ubuntu@34.236.90.56

# Ver logs do deployment
tail -f /home/ubuntu/ss-fleet-core/deploy.log

# Ver status dos containers
docker-compose ps

# Ver logs da aplicação
docker-compose logs -f api
```

---

## 📊 Portas Configuradas

### ⚠️ REQUISITO CRÍTICO: PORTA 8000 DA API

**A PORTA 8000 É FIXA E NÃO PODE SER ALTERADA**

Esta porta está **BLOQUEADA** e **NÃO DEVE MUDAR** em nenhuma circunstância:

- ❌ **NÃO** usar resolução automática de conflitos para porta 8000
- ❌ **NÃO** permitir que a API rode em porta alternativa
- ❌ **NÃO** modificar docker-compose.yml para usar porta diferente
- ✅ **SEMPRE** validar porta 8000 após deployment: `bash .scripts/validate-port.sh`

**Motivos para porta fixa:**
- Integrações externas dependem da porta 8000
- Configurações de rede e firewall estão configuradas para porta 8000
- Documentação de clientes referencia porta 8000
- SSL/TLS certificates podem estar vinculados à porta 8000

**Se porta 8000 estiver ocupada:**
1. Identificar processo que está usando: `sudo lsof -i :8000`
2. Parar o processo conflitante (se seguro)
3. Se não puder parar, **deployment DEVE FALHAR** - não usar porta alternativa
4. Nunca iniciar a API em outra porta

### Portas Padrão (Configuração Base)
- **8000**: API FastAPI ⚠️ **PORTA FIXA - NUNCA ALTERAR**
- **6380**: Redis (porta externa, interna 6379)
- **9091**: Prometheus (porta externa, interna 9090)

**Nota:** As portas externas do Redis (6380) e Prometheus (9091) foram alteradas para evitar conflitos com serviços do sistema. Dentro da rede Docker, os containers se comunicam pelas portas internas (6379 e 9090).

### Port Conflict Resolution (Apenas Redis e Prometheus)

O script `deploy.sh` automaticamente verifica portas disponíveis **APENAS** para Redis e Prometheus:
1. Verifica se cada porta está disponível
2. Se ocupada, busca porta alternativa (+1 até +100)
3. Atualiza `docker-compose.yml` com novas portas
4. Salva configuração em `.ports` no servidor

⚠️ **IMPORTANTE**: A porta 8000 da API **NUNCA** passa por esse processo de resolução de conflitos.

**Exemplo de `.ports` gerado:**
```
API_PORT=8000        # SEMPRE 8000 - nunca muda
REDIS_PORT=6379
PROMETHEUS_PORT=9090
```

**Se houver conflito (apenas Redis/Prometheus podem mudar):**
```
API_PORT=8000        # SEMPRE 8000 - NUNCA MUDA
REDIS_PORT=6380
PROMETHEUS_PORT=9091
```

---

## 🔒 Segurança

### Secrets Manager
- Chave SSH armazenada em: `fleet-ssh-key`
- Nunca commitar `ss-gateway-jimi.pem` no repositório
- Lambda recupera chave via Secrets Manager

### IAM Role (fleet-lambda-deploy-role)
Permissões:
- `AWSLambdaBasicExecutionRole`: Logs no CloudWatch
- `secretsmanager:GetSecretValue`: Acesso à chave SSH
- `secretsmanager:DescribeSecret`: Metadados do secret
- `ec2:DescribeInstances`: Informações do EC2
- `ec2:DescribeNetworkInterfaces`: Informações de rede

### Network Security
- Lambda conecta via IP privado (172.31.85.75)
- EC2 Security Group deve permitir SSH da Lambda
- Containers expostos apenas em localhost (exceto se configurado diferente)

---

## 🛠️ Troubleshooting

### Deployment falhou

**1. Verificar logs da Lambda:**
```
AWS Console → Lambda → fleet-codecommit-deploy-trigger → Monitor → View CloudWatch logs
```

**2. Verificar logs no servidor:**
```bash
ssh -i ss-gateway-jimi.pem ubuntu@34.236.90.56
tail -f /home/ubuntu/ss-fleet-core/deploy.log
```

**3. Erros comuns:**

#### Health check timeout
```
ERROR: API health check failed after 30 attempts
```
**Solução:**
- Verificar se `.env` está configurado corretamente
- Verificar conexão com Aurora PostgreSQL
- Verificar logs do container: `docker-compose logs api`

**Solução:** Normal. Script encontra porta alternativa automaticamente.

#### Git pull failed
```
ERROR: Git pull failed
```
**Solução:**
- Verificar configuração do Git no servidor
- Verificar credenciais AWS no servidor
- Executar manualmente: `cd /home/ubuntu/ss-fleet-core && git pull`

#### Docker build failed
```
ERROR: Docker build failed
```
**Solução:**
- Verificar sintaxe do Dockerfile
- Verificar dependências em requirements.txt
- Verificar espaço em disco: `df -h`

### Rollback Manual

Se deployment automático falhar e precisar fazer rollback:

```bash
# Conectar ao servidor
ssh -i ss-gateway-jimi.pem ubuntu@34.236.90.56
cd /home/ubuntu/ss-fleet-core

# Voltar para commit anterior
git log --oneline -5  # Ver últimos commits
git checkout <commit-hash-anterior>

# Rebuild e restart
docker-compose down
docker-compose build
docker-compose up -d

# Verificar saúde
curl http://localhost:8000/health
```

---

## 📝 Logs e Monitoramento

### CloudWatch Logs
- **Lambda**: `/aws/lambda/fleet-codecommit-deploy-trigger`
- Retention: 7 dias (configurável)

### Server Logs
```bash
# Deployment log
tail -f /home/ubuntu/ss-fleet-core/deploy.log

# Application logs
docker-compose logs -f api

# Redis logs
docker-compose logs -f redis

# Prometheus logs
docker-compose logs -f prometheus

# All containers
docker-compose logs -f
```

### Health Checks
```bash
# API health
curl http://localhost:8000/health

# Response esperado:
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "version": "0.1.0"
}
```

### Prometheus Metrics
- URL: http://34.236.90.56:9090
- Métricas disponíveis:
  - `http_requests_total`
  - `http_request_duration_seconds`
  - `http_requests_in_progress`
  - `http_response_size_bytes`

---

## 🔄 Atualizações e Manutenção

### Atualizar Lambda Function

```bash
# Após modificar scripts/lambda/deploy_fleet_trigger.py
./scripts/aws_setup.sh
# Script detecta e atualiza função existente
```

### Atualizar Chave SSH

```bash
# 1. Atualizar ss-gateway-jimi.pem no projeto
# 2. Re-executar setup
./scripts/aws_setup.sh
# Script atualiza secret existente
```

### Remover Deployment Automático

```bash
# Remover trigger do CodeCommit
aws codecommit put-repository-triggers \
  --repository-name ss-fleet-core \
  --triggers '[]' \
  --region us-east-1

# Deletar Lambda function
aws lambda delete-function \
  --function-name fleet-codecommit-deploy-trigger \
  --region us-east-1

# Deletar IAM Role
aws iam detach-role-policy \
  --role-name fleet-lambda-deploy-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam delete-role \
  --role-name fleet-lambda-deploy-role

# Deletar Secret
aws secretsmanager delete-secret \
  --secret-id fleet-ssh-key \
  --force-delete-without-recovery \
  --region us-east-1
```

---

## 🚦 Checklist de Produção

Antes de fazer deployment em produção:

- [ ] `.env` configurado com valores de produção
- [ ] `SECRET_KEY` e `JWT_SECRET_KEY` gerados com alta entropia
- [ ] `DEBUG=false`
- [ ] `ENVIRONMENT=production`
- [ ] Conexão com Aurora PostgreSQL testada
- [ ] Redis funcionando
- [ ] CORS_ORIGINS configurado com domínio real
- [ ] ⚠️ **VALIDAR PORTA 8000** com `bash .scripts/validate-port.sh`
- [ ] Health check respondendo corretamente em `http://servidor:8000/health`
- [ ] Backup do Aurora configurado
- [ ] Monitoring configurado (CloudWatch, Prometheus)
- [ ] SSL/TLS configurado no load balancer (se aplicável)
- [ ] Security groups configurados corretamente
- [ ] Logs sendo coletados e armazenados
- [ ] ⚠️ **Confirmar que nenhum outro processo está usando porta 8000**

---

## 📞 Suporte

**Logs importantes:**
- Lambda: CloudWatch `/aws/lambda/fleet-codecommit-deploy-trigger`
- Servidor: `/home/ubuntu/ss-fleet-core/deploy.log`
- Aplicação: `docker-compose logs api`

**Comandos úteis:**
```bash
# Status dos containers
docker-compose ps

# Restart completo
docker-compose restart

# Ver uso de recursos
docker stats

# Limpar recursos não utilizados
docker system prune -a
```

---

**Última atualização:** 2025-01-14
**Versão:** 1.0.0
**Status:** ✅ Produção-Ready
