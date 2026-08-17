# 🤖 Testes Automáticos - Guia Simples

## Como Funcionam os Testes Automáticos?

### ✅ Opção 1: Manual (Você roda quando quiser)

```bash
# Todos os testes
pytest tests/ -v

# Apenas testes de segurança
pytest tests/test_access_control_security.py -v

# Apenas testes de veículos (CRUD)
pytest tests/test_vehicles_api.py -v

# Com coverage
pytest tests/ --cov=app --cov-report=html
```

**Quando usar**: Sempre que você fizer alterações e quiser verificar.

---

### 🔄 Opção 2: Automático com Git Hooks (IMPLEMENTADO)

Os testes rodam **automaticamente** quando você faz commit ou push.

## Como Foi Configurado

### 1. Pre-Commit Hook (Antes de Commit)

**O que acontece**:
```bash
$ git add .
$ git commit -m "minha alteração"

# ⬇️ AUTOMÁTICO - Você não precisa fazer nada!
🔒 Running security and CRUD tests...
Running tests inside Docker container...
============================= test session starts ==============================
tests/test_access_control_security.py ................ PASSED
tests/test_vehicles_api.py ............... PASSED
============================== 35 passed in 1.60s ==============================

✅ All tests passed! Proceeding with commit.
```

**Se tiver erro**:
```bash
$ git commit -m "código com bug"

# ⬇️ AUTOMÁTICO
🔒 Running security tests...
❌ Security tests failed! Commit blocked.
# Commit NÃO é criado até você corrigir!
```

### 2. Pre-Push Hook (Antes de Push)

**O que acontece**:
```bash
$ git push origin main

# ⬇️ AUTOMÁTICO
🧪 Running all tests before push...
✅ All tests passed! Proceeding with push.
```

---

## ⚙️ Instalação dos Hooks

### Já Instalado! ✅

Os hooks já foram criados em:
- `.git/hooks/pre-commit` ✅
- `.git/hooks/pre-push` ✅

### Para Reinstalar (Se Necessário)

```bash
./scripts/setup-git-hooks.sh
```

---

## 🎯 Quando os Testes Rodam?

| Ação | Testes Rodam? | O Que Testa |
|------|---------------|-------------|
| `git commit` | ✅ SIM | Testes de segurança + CRUD (35 testes, ~2s) |
| `git push` | ✅ SIM | Todos os testes (pode levar mais tempo) |
| Salvar arquivo | ❌ NÃO | Nada |
| `git add` | ❌ NÃO | Nada |
| `git status` | ❌ NÃO | Nada |

### Testes Incluídos

**Pre-Commit (35 testes):**
- ✅ 20 testes de segurança (controle de acesso)
- ✅ 15 testes de CRUD de veículos (soft delete, audit trail, etc.)

**Pre-Push (todos os testes):**
- ✅ Todos os testes do pre-commit
- ✅ Testes adicionais (se houver)

---

## 🚫 Pular os Testes (Emergência)

Se você **REALMENTE** precisa fazer commit sem rodar testes:

```bash
# ATENÇÃO: Use apenas em emergências!
git commit --no-verify -m "hotfix urgente"
```

⚠️ **Não recomendado** - só em casos excepcionais!

---

## 🔧 Gerenciar Hooks

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

### Remover Completamente

```bash
rm .git/hooks/pre-commit
rm .git/hooks/pre-push
```

---

## 📊 Resumo Visual

### Fluxo Normal (Com Hooks Ativos)

```
Você:  git add .
Você:  git commit -m "mensagem"
       ↓
Hook:  🔒 Rodando testes...
       ↓
       ├─ ✅ Passou → Commit criado
       └─ ❌ Falhou → Commit bloqueado

Você:  git push origin main
       ↓
Hook:  🧪 Rodando todos os testes...
       ↓
       ├─ ✅ Passou → Push realizado
       └─ ❌ Falhou → Push bloqueado
```

---

## ❓ FAQ Rápido

**P: Os testes rodam sozinhos?**
R: Sim, automaticamente quando você faz `git commit` ou `git push`.

**P: Posso desabilitar?**
R: Sim, mas não é recomendado. Use `git commit --no-verify` em emergências.

**P: Demora quanto tempo?**
R: Pre-commit: ~5 segundos. Pre-push: ~10-30 segundos (depende da quantidade de testes).

**P: O que fazer se os testes falharem?**
R: Corrija o código até os testes passarem. Os erros aparecem no terminal.

**P: Funciona no Windows/Mac/Linux?**
R: Sim, funciona em todos os sistemas operacionais.

---

## 📚 Mais Informações

- **Guia Completo**: [GIT_HOOKS_GUIDE.md](GIT_HOOKS_GUIDE.md)
- **Testes**: [test_access_control_security.py](../tests/test_access_control_security.py)
- **Segurança**: [SECURITY.md](../SECURITY.md)

---

**Criado**: 2025-11-16
