#!/usr/bin/env bash
#
# DS-1376 (épico DS-1342): garante DEBUG=false no .env de produção.
#
# ATENÇÃO - leia antes de rodar: a descrição original do ticket pede para
# confirmar com o dev leader se DEBUG=true e/ou reload=True são
# intencionais antes de aplicar esta mudança. Este script assume que essa
# confirmação já foi feita e que a resposta foi "não, não é intencional".
# Se ainda não foi confirmado, pare aqui e confirme antes de rodar em
# produção - depois de aplicado, /docs, /rapidoc, /docs-info e
# /openapi.json passam a responder 404 publicamente.
#
# O que o script faz:
#   - Se a chave DEBUG já existe no .env, substitui o valor por "false"
#     (não mexe em nenhuma outra linha).
#   - Se a chave não existe, adiciona "DEBUG=false" ao final do arquivo.
#   - Cria backup automático antes de qualquer alteração.
#
# Uso:
#   ./scripts/set_debug_false.sh [caminho-para-o-.env]
#
# Padrão do caminho: .env (diretório atual)

set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERRO: arquivo não encontrado: $ENV_FILE" >&2
    exit 1
fi

CURRENT_VALUE="$(grep -E '^DEBUG=' "$ENV_FILE" | tail -n1 | cut -d'=' -f2- || true)"

if [[ "$CURRENT_VALUE" == "false" ]]; then
    echo "DEBUG já está 'false' em $ENV_FILE. Nada a fazer."
    exit 0
fi

BACKUP_FILE="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp "$ENV_FILE" "$BACKUP_FILE"
echo "Backup criado em: $BACKUP_FILE"

if grep -qE '^DEBUG=' "$ENV_FILE"; then
    # Substitui apenas a linha DEBUG=..., preservando o resto do arquivo
    sed -i -E 's/^DEBUG=.*/DEBUG=false/' "$ENV_FILE"
else
    # Chave ausente - adiciona ao final
    printf '\nDEBUG=false\n' >> "$ENV_FILE"
fi

echo "DEBUG definido como 'false' em $ENV_FILE."
echo "Revise o diff antes de reiniciar os serviços:"
diff -u "$BACKUP_FILE" "$ENV_FILE" || true
echo ""
echo "Próximo passo: docker-compose restart api && bash .scripts/validate-port.sh"