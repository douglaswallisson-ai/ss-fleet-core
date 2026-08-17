#!/usr/bin/env bash
#
# DS-1381 (épico DS-1342): remove as variáveis mortas JWT_SECRET_KEY e
# JWT_ALGORITHM de um arquivo .env.
#
# Essas variáveis não são lidas por app/core/config.py (que usa SECRET_KEY
# e ALGORITHM) e são silenciosamente ignoradas pelo Pydantic Settings
# (extra="ignore"). Este script só remove as linhas; não reinicia serviços.
#
# Uso:
#   ./scripts/remove_dead_env_vars.sh [caminho-para-o-.env]
#
# Padrão do caminho: .env (diretório atual)
#
# O script é idempotente: rodar novamente em um arquivo já limpo não faz
# nada além de reportar que nenhuma variável morta foi encontrada.

set -euo pipefail

ENV_FILE="${1:-.env}"
DEAD_VARS=("JWT_SECRET_KEY" "JWT_ALGORITHM")

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERRO: arquivo não encontrado: $ENV_FILE" >&2
    exit 1
fi

FOUND=0
for var in "${DEAD_VARS[@]}"; do
    if grep -qE "^${var}=" "$ENV_FILE"; then
        FOUND=1
    fi
done

if [[ "$FOUND" -eq 0 ]]; then
    echo "Nenhuma variável morta (${DEAD_VARS[*]}) encontrada em $ENV_FILE. Nada a fazer."
    exit 0
fi

BACKUP_FILE="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp "$ENV_FILE" "$BACKUP_FILE"
echo "Backup criado em: $BACKUP_FILE"

TMP_FILE="$(mktemp)"
grep -vE "^(JWT_SECRET_KEY|JWT_ALGORITHM)=" "$ENV_FILE" > "$TMP_FILE"
mv "$TMP_FILE" "$ENV_FILE"

echo "Variáveis removidas de $ENV_FILE: ${DEAD_VARS[*]}"
echo "Revise o diff antes de reiniciar os serviços:"
diff -u "$BACKUP_FILE" "$ENV_FILE" || true