"""
Escopo de acesso por grupo e subgrupo.

Centraliza a leitura do `group_access` que o middleware de autenticação já
carrega, e a montagem da cláusula SQL correspondente.

Existe porque a alternativa — cada endpoint montando o próprio filtro — já
produziu dois erros: um lendo apenas `group_id` e ignorando o subgrupo, e
outros quatro sem filtro nenhum, que devolviam dados de todos os clientes a
qualquer usuário autenticado.
"""

from typing import Optional

from app.core.query_filters import SQLAccessControlBuilder


def escopo_do_usuario(user) -> tuple[list[int], Optional[list[int]]]:
    """
    Grupos e subgrupos que o usuário enxerga.

    Delega para `SQLAccessControlBuilder`, que é a implementação já validada do
    repositório. Eu havia escrito uma versão própria e ela divergia num ponto
    decisivo: subgrupo vazio.

    Quando o usuário tem acesso a um grupo **sem subgrupo definido**
    — `(14331, None)` — ele enxerga o grupo inteiro. A lista de subgrupos volta
    como `None`, e o SQL trata `None` como "sem restrição de subgrupo". Uma
    lista vazia significaria o oposto: nenhum subgrupo permitido.

    Master vê tudo; grupos vazios sinalizam ausência de filtro.
    """
    if getattr(user, "master", 0):
        return [], None

    acesso = getattr(user, "group_access", None) or []
    if not acesso:
        # Sem nenhum acesso concedido, o correto é não devolver nada — e não
        # tudo. Grupo inexistente garante resultado vazio.
        return [-1], None

    return SQLAccessControlBuilder.extract_accessible_groups_and_subgroups(acesso)


def clausula_escopo(
    grupos: list[int],
    subgrupos: Optional[list[int]],
    alias: str,
    coluna_grupo: str = "group_id",
    coluna_subgrupo: str = "subgroup_id",
) -> tuple[str, dict]:
    """
    Cláusula SQL de escopo, pronta para concatenar num WHERE.

    Segue exatamente a forma da implementação oficial:

        group_id = ANY(:grupos)
        AND (:subgrupos IS NULL OR subgroup_id = ANY(:subgrupos))

    O `IS NULL` no meio é o que faz a diferença. Com subgrupos nulos, a segunda
    condição é sempre verdadeira e o usuário vê o grupo inteiro — que é o caso
    de quem tem acesso concedido no nível do grupo. Com lista preenchida, ele vê
    só os subgrupos dela.

    Eu havia escrito isso como OR entre as duas condições, o que deixava passar
    qualquer registro do grupo mesmo quando o acesso era restrito a alguns
    subgrupos. Era vazamento dentro da mesma conta.

    Devolve string vazia quando não há filtro a aplicar (master).
    """
    if not grupos:
        return "", {}

    return (
        f" AND {alias}.{coluna_grupo} = ANY(:escopo_grupos)"
        f" AND (:escopo_subgrupos IS NULL OR {alias}.{coluna_subgrupo} = ANY(:escopo_subgrupos))",
        {"escopo_grupos": grupos, "escopo_subgrupos": subgrupos},
    )
