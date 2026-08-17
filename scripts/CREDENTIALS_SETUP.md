# AWS Credentials Setup Guide

⚠️ **NOTA:** Este guia está **DESATUALIZADO**. A Lambda foi simplificada e não configura mais AWS CLI ou Git no servidor.

---

## ✅ Nova Abordagem (Simplificada)

A Lambda agora assume que o servidor **já está configurado** manualmente. Ela apenas:
1. Conecta via SSH
2. Faz `git pull`
3. Executa `scripts/deploy.sh`

**Configurações necessárias no servidor (manual):**
- ✅ AWS CLI configurado com credenciais CodeCommit
- ✅ Git configurado com credential helper
- ✅ Docker e Docker Compose instalados
- ✅ Usuário com acesso sudo

---

## 🔐 Setup Manual no Servidor (One-Time)

### Passo 1: Conectar ao Servidor

```bash
ssh -i .secrets/ss-gateway-jimi.pem ubuntu@34.236.90.56
```

### Passo 2: Configurar AWS CLI (se ainda não configurado)

```bash
aws configure
# Insira as credenciais fornecidas pelo administrador
```

### Passo 3: Configurar Git (se ainda não configurado)

```bash
git config --global credential.helper '!aws codecommit credential-helper $@'
git config --global credential.UseHttpPath true
```

### Passo 4: Testar Acesso ao CodeCommit

```bash
cd /home/ubuntu/ss-fleet-core
git pull origin main
# Deve funcionar sem pedir senha
```

---

## ~~🔐 Setup de Credenciais no Secrets Manager~~ (DESATUALIZADO)

~~As credenciais AWS foram **removidas do código** por segurança. Agora elas devem ser armazenadas no AWS Secrets Manager.~~

**NOTA:** Isso não é mais necessário. A Lambda não busca credenciais AWS do Secrets Manager.

---

## 🔐 Setup de Credenciais no Secrets Manager

### Passo 1: Criar Secret para Credenciais AWS

```bash
# Criar secret com credenciais AWS
aws secretsmanager create-secret \
  --name fleet-aws-credentials \
  --description "AWS credentials for CodeCommit access in Fleet deployment" \
  --secret-string '{"access_key":"YOUR_ACCESS_KEY_ID","secret_key":"YOUR_SECRET_ACCESS_KEY"}' \
  --region us-east-1
```

**Formato do secret:**
```json
{
  "access_key": "AKIAXXXXXXXXXXXXXXXX",
  "secret_key": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

---

## 🔄 Opção Alternativa: Variáveis de Ambiente Lambda

Se preferir usar variáveis de ambiente da Lambda ao invés de Secrets Manager:

### Via AWS Console

1. Acesse: AWS Console → Lambda → `fleet-codecommit-deploy-trigger`
2. Configuration → Environment variables
3. Adicione:
   - `AWS_ACCESS_KEY_ID`: Sua access key
   - `AWS_SECRET_ACCESS_KEY`: Sua secret key

### Via AWS CLI

```bash
aws lambda update-function-configuration \
  --function-name fleet-codecommit-deploy-trigger \
  --environment "Variables={AWS_ACCESS_KEY_ID=YOUR_ACCESS_KEY,AWS_SECRET_ACCESS_KEY=YOUR_SECRET_KEY}" \
  --region us-east-1
```

---

## 🔍 Verificar Configuração

### Verificar Secret no Secrets Manager

```bash
# Verificar se secret existe
aws secretsmanager describe-secret \
  --secret-id fleet-aws-credentials \
  --region us-east-1

# Ver valor do secret (cuidado - mostra credenciais!)
aws secretsmanager get-secret-value \
  --secret-id fleet-aws-credentials \
  --region us-east-1 \
  --query SecretString \
  --output text
```

### Verificar Variáveis de Ambiente Lambda

```bash
aws lambda get-function-configuration \
  --function-name fleet-codecommit-deploy-trigger \
  --region us-east-1 \
  --query 'Environment'
```

---

## 🔄 Atualizar Lambda após Setup de Credenciais

Após criar o secret no Secrets Manager, atualize a Lambda:

```bash
# Repackage e redeploy da Lambda
cd /caminho/do/projeto
./scripts/aws_setup.sh
```

---

## 🔒 Permissões IAM Necessárias

A IAM Role da Lambda (`fleet-lambda-deploy-role`) precisa ter permissão para acessar o secret:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:*:secret:fleet-aws-credentials-*"
    }
  ]
}
```

**Nota:** O script `aws_setup.sh` já cria uma policy similar. Verifique se inclui o novo secret `fleet-aws-credentials`.

---

## 🧪 Testar Deployment

Após configurar as credenciais:

1. **Fazer um commit e push para testar:**
   ```bash
   echo "# Test deployment" >> README.md
   git add README.md
   git commit -m "test: deployment automático com credenciais do Secrets Manager"
   git push origin main
   ```

2. **Monitorar logs da Lambda:**
   ```
   AWS Console → CloudWatch → Log groups → /aws/lambda/fleet-codecommit-deploy-trigger
   ```

3. **Verificar no servidor:**
   ```bash
   ssh -i .secrets/ss-gateway-jimi.pem ubuntu@34.236.90.56
   tail -f /home/ubuntu/ss-fleet-core/deploy.log
   ```

---

## 🔧 Troubleshooting

### Erro: "Failed to retrieve AWS credentials"

**Causa:** Secret não existe ou Lambda não tem permissão

**Solução:**
1. Verificar se secret existe: `aws secretsmanager describe-secret --secret-id fleet-aws-credentials`
2. Verificar permissões IAM da Lambda
3. Verificar formato do secret (deve ser JSON com keys `access_key` e `secret_key`)

### Erro: "Git pull failed"

**Causa:** Credenciais AWS incorretas ou expiradas

**Solução:**
1. Verificar credenciais no Secrets Manager
2. Gerar novas credenciais AWS se necessário
3. Atualizar secret com novas credenciais

### Lambda usa variáveis de ambiente mas não Secrets Manager

**Comportamento esperado:** Lambda tenta Secrets Manager primeiro, depois fallback para env vars

**Logs para verificar:**
```
WARNING: Using AWS credentials from environment variables
```

---

## 📝 Rotação de Credenciais

Para rotacionar as credenciais AWS:

1. **Gerar novas credenciais** no AWS Console (IAM)

2. **Atualizar Secrets Manager:**
   ```bash
   aws secretsmanager update-secret \
     --secret-id fleet-aws-credentials \
     --secret-string '{"access_key":"NEW_KEY","secret_key":"NEW_SECRET"}' \
     --region us-east-1
   ```

3. **Testar deployment** (fazer um commit de teste)

4. **Deletar credenciais antigas** no AWS Console

---

## 🔗 Documentação Adicional

- [DEPLOYMENT.md](../DEPLOYMENT.md) - Guia completo de deployment
- [aws_setup.sh](aws_setup.sh) - Script de setup da infraestrutura AWS
- [lambda/deploy_fleet_trigger.py](lambda/deploy_fleet_trigger.py) - Código da função Lambda

---

**Última atualização:** 2025-11-24
