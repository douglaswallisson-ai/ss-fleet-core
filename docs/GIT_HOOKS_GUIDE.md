# Git Hooks - Automated Testing Guide

## O Que São Git Hooks?

Git hooks são **scripts automáticos** que rodam em momentos específicos do workflow do Git (commit, push, etc.).

---

## 🔄 Hooks Instalados

### 1. Pre-Commit Hook

**Quando roda**: Antes de cada `git commit`

**O que faz**: Executa testes de segurança

**Exemplo**:
```bash
$ git add .
$ git commit -m "minha alteração"

🔒 Running security tests...
========================= test session starts ==========================
tests/test_access_control_security.py::TestSubgroupAccessValidation::test_user_with_null_subgroup_cannot_request_specific_subgroups PASSED
... (mais testes)

✅ Security tests passed! Proceeding with commit.
[main abc1234] minha alteração
```

**Se os testes falharem**:
```bash
$ git commit -m "código com bug"

🔒 Running security tests...
FAILED tests/test_access_control_security.py::test_some_security_check

❌ Security tests failed! Commit blocked.
Fix the issues before committing.
```

### 2. Pre-Push Hook

**Quando roda**: Antes de cada `git push`

**O que faz**: Executa TODOS os testes

**Exemplo**:
```bash
$ git push origin main

🧪 Running all tests before push...
========================= test session starts ==========================
tests/test_access_control_security.py ... PASSED
... (todos os testes)

✅ All tests passed! Proceeding with push.
```

---

## 📦 Instalação

### Automática (Recomendado)

```bash
# Execute o script de setup
./scripts/setup-git-hooks.sh
```

### Manual

```bash
# Copiar hooks manualmente
cp scripts/git-hooks/pre-commit .git/hooks/
cp scripts/git-hooks/pre-push .git/hooks/
chmod +x .git/hooks/pre-commit
chmod +x .git/hooks/pre-push
```

---

## 🎮 Como Usar

### Uso Normal (Com Hooks Ativos)

```bash
# Fazer commit (roda testes de segurança automaticamente)
git add .
git commit -m "minha feature"

# Push (roda todos os testes automaticamente)
git push origin main
```

### Pular os Hooks (Emergência)

Se você **realmente precisa** fazer commit sem rodar os testes:

```bash
# ATENÇÃO: Use apenas em emergências!
git commit --no-verify -m "hotfix urgente"
git push --no-verify
```

⚠️ **Cuidado**: Isso pula as verificações de segurança!

---

## 🔧 Gerenciamento de Hooks

### Ver Hooks Instalados

```bash
ls -la .git/hooks/
```

### Desabilitar Temporariamente

```bash
# Renomear para desabilitar
mv .git/hooks/pre-commit .git/hooks/pre-commit.disabled
```

### Reabilitar

```bash
# Renomear de volta
mv .git/hooks/pre-commit.disabled .git/hooks/pre-commit
```

### Desinstalar Completamente

```bash
# Remover hooks
rm .git/hooks/pre-commit
rm .git/hooks/pre-push
```

---

## 🐛 Troubleshooting

### "pytest: command not found"

**Problema**: Python/pytest não está no PATH

**Solução**:
```bash
# Ativar ambiente virtual
source venv/bin/activate

# Ou instalar dependências
pip install -r requirements-dev.txt
```

### Hooks não estão rodando

**Verificar**:
```bash
# Conferir se tem permissão de execução
ls -la .git/hooks/pre-commit

# Deve mostrar: -rwxr-xr-x (com 'x' = executável)
```

**Corrigir**:
```bash
chmod +x .git/hooks/pre-commit
chmod +x .git/hooks/pre-push
```

### Testes levam muito tempo

**Opção 1**: Rodar apenas testes rápidos no pre-commit
```bash
# Editar .git/hooks/pre-commit
# Mudar linha:
pytest tests/test_access_control_security.py -v
# Para:
pytest tests/test_access_control_security.py -v -k "not slow"
```

**Opção 2**: Desabilitar pre-commit, manter apenas pre-push
```bash
rm .git/hooks/pre-commit
# Agora só roda testes no push
```

---

## 📊 Fluxo de Trabalho

### Desenvolvimento Normal

```
1. Fazer alterações no código
   ↓
2. git add .
   ↓
3. git commit -m "mensagem"
   ↓
4. 🔒 Pre-commit hook roda testes de segurança
   ↓
   ├─ ✅ Testes passam → Commit criado
   └─ ❌ Testes falham → Commit bloqueado, corrigir código
   ↓
5. git push origin main
   ↓
6. 🧪 Pre-push hook roda todos os testes
   ↓
   ├─ ✅ Testes passam → Push realizado
   └─ ❌ Testes falham → Push bloqueado, corrigir código
```

### Emergência (Skip Hooks)

```
1. Hotfix urgente necessário
   ↓
2. git commit --no-verify -m "hotfix"
   ↓
3. git push --no-verify
   ↓
4. ⚠️ IMPORTANTE: Rodar testes depois manualmente!
   ↓
5. pytest tests/ -v
```

---

## 🎯 Benefícios

✅ **Previne bugs em produção**: Testes rodam antes do código sair da sua máquina

✅ **Garante segurança**: Vulnerabilidades são detectadas imediatamente

✅ **Economia de tempo**: Detecta problemas cedo (antes do CI/CD)

✅ **Código mais limpo**: Força boas práticas

✅ **Confiança**: Sabe que se commitou, os testes passaram

---

## 📝 Customização

### Adicionar Mais Testes ao Pre-Commit

Edite `.git/hooks/pre-commit`:

```bash
#!/bin/bash
echo "🔒 Running security tests..."
pytest tests/test_access_control_security.py -v

echo "🎨 Running linters..."
flake8 app/
black --check app/

echo "🔍 Running type checks..."
mypy app/

# Se qualquer um falhar, bloqueia commit
exit 0
```

### Criar Hook Customizado

```bash
# Criar novo hook
cat > .git/hooks/pre-merge-commit << 'EOF'
#!/bin/bash
echo "Running tests before merge..."
pytest tests/ -v
EOF

chmod +x .git/hooks/pre-merge-commit
```

---

## 🔗 Referências

- [Git Hooks Documentation](https://git-scm.com/book/en/v2/Customizing-Git-Git-Hooks)
- Pytest: `pytest --help`
- Hooks disponíveis: `ls /usr/share/git-core/templates/hooks/`

---

## ⚙️ Configuração para Equipe

Para garantir que toda a equipe use os mesmos hooks:

1. **Adicionar ao onboarding**:
   ```bash
   # No README.md
   "Após clonar o repositório, execute:
   ./scripts/setup-git-hooks.sh"
   ```

2. **Documentar no Contributing Guide**

3. **Opcional**: Usar ferramenta como [pre-commit](https://pre-commit.com/)
   ```bash
   pip install pre-commit
   pre-commit install
   ```

---

**Última atualização**: 2025-11-16
