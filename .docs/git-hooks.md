# Git Hooks - Automated Testing

## Pre-Commit Hook

O projeto está configurado com um **pre-commit hook** que executa automaticamente todos os testes antes de permitir um commit.

### Como Funciona

Quando você executa `git commit`, o hook:

1. ✅ Executa todos os testes com `pytest tests/ -v --cov=app`
2. ✅ Verifica se todos os testes passaram
3. ✅ Se todos passarem: **Commit é permitido**
4. ❌ Se algum falhar: **Commit é cancelado**

### Exemplo de Uso

#### Cenário 1: Todos os testes passam
```bash
$ git commit -m "feat: add new feature"
🧪 Running tests before commit...

tests/test_driver_api.py::TestDriverBusinessRules::test_soft_delete_pattern PASSED
tests/test_driver_api.py::TestDriverBusinessRules::test_cnh_expiration_validation PASSED
...
✅ All tests passed! Proceeding with commit...

[main abc1234] feat: add new feature
```

#### Cenário 2: Algum teste falha
```bash
$ git commit -m "feat: add new feature"
🧪 Running tests before commit...

tests/test_driver_api.py::TestDriverBusinessRules::test_soft_delete_pattern FAILED
...
❌ COMMIT CANCELLED: Tests failed!

Please fix the failing tests before committing.
To commit anyway (not recommended), use: git commit --no-verify
```

### Bypass do Hook (Emergência)

Se precisar fazer commit **sem rodar os testes** (não recomendado):

```bash
git commit --no-verify -m "emergency fix"
```

⚠️ **Use com cautela!** Isso pode introduzir código com bugs no repositório.

### Localização do Hook

O hook está localizado em:
```
.git/hooks/pre-commit
```

### Desativar o Hook

Para desativar temporariamente:
```bash
chmod -x .git/hooks/pre-commit
```

Para reativar:
```bash
chmod +x .git/hooks/pre-commit
```

### Requisitos

- Docker e Docker Compose instalados (recomendado)
- OU Python 3.11+ com pytest instalado (`pip install -r requirements-dev.txt`)
- Todos os testes devem estar no diretório `tests/`

### Como Funciona

O hook verifica automaticamente se o container Docker `fleet_api` está rodando:
- ✅ Se estiver: executa os testes dentro do container
- ⚠️ Se não estiver: tenta executar localmente (requer pytest instalado)
- ❌ Se nenhum estiver disponível: cancela o commit com instruções

## Benefícios

- 🛡️ Previne commits com código quebrado
- ✅ Garante que todos os testes passam antes de integrar
- 📊 Fornece feedback imediato sobre problemas
- 🚀 Mantém a qualidade do código alta
