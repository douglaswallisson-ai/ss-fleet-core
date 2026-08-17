#!/usr/bin/env bash
#
# DS-1383 (épico DS-1342): verifica se algum arquivo de texto rastreado
# pelo Git ainda contém CRLF, após a normalização feita por .gitattributes.
#
# Serve tanto para checagem manual quanto para uso em CI (falha com exit
# code 1 se encontrar algum arquivo de texto com CR).
#
# Uso:
#   ./scripts/check_line_endings.sh
#
# Requisitos: rodar dentro de um repositório Git (usa `git ls-files`).

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

# Extensões binárias conhecidas (mesmas marcadas como `binary` no
# .gitattributes) - não fazem sentido nesta checagem.
BINARY_PATTERN='\.(png|jpg|jpeg|gif|ico|pdf|zip|gz|pem|p12|woff|woff2)$'

OFFENDERS=()

while IFS= read -r -d '' file; do
    # Pula arquivos binários conhecidos
    if [[ "$file" =~ $BINARY_PATTERN ]]; then
        continue
    fi

    # Pula se o próprio git identificar como binário
    if git check-attr binary -- "$file" | grep -q "binary: set"; then
        continue
    fi

    if [[ -f "$file" ]] && grep -qU $'\r' "$file" 2>/dev/null; then
        OFFENDERS+=("$file")
    fi
done < <(git ls-files -z)

if [[ ${#OFFENDERS[@]} -gt 0 ]]; then
    echo "ERRO: os arquivos abaixo ainda contêm CRLF (esperado: LF):" >&2
    printf ' - %s\n' "${OFFENDERS[@]}" >&2
    echo "" >&2
    echo "Rode: git add --renormalize . && git commit -m 'Normalizar CRLF->LF'" >&2
    exit 1
fi

echo "OK: nenhum arquivo de texto rastreado com CRLF encontrado."
exit 0