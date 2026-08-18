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


def escopo_do_usuario(user) -> tuple[list[int], list[int]]:
    """
    Grupos e subgrupos que o usuário enxerga.

    Master vê tudo, e listas vazias sinalizam ausência de filtro.

    Na prática um usuário costuma ter acesso a **um grupo e dezenas de
    subgrupos** dentro dele: a granularidade real está no subgrupo, e
    ignorá-lo dá acesso amplo demais a quem deveria ver poucas unidades.
    """
    if getattr(user, "master", 0):
        return [], []

    acesso = getattr(user, "group_access", None) or []
    grupos = sorted({g for g, _ in acesso if g is not None})
    subgrupos = sorted({sg for _, sg in acesso if sg is not None})
    return grupos, subgrupos


def clausula_escopo(
    grupos: list[int],
    subgrupos: list[int],
    alias: str,
    coluna_grupo: str = "group_id",
    coluna_subgrupo: Optional[str] = "subgroup_id",
) -> tuple[str, dict]:
    """
    Cláusula SQL de escopo, pronta para concatenar num WHERE.

    Grupo e subgrupo entram em OR, não em AND: o registro pertence ao escopo se
    estiver num grupo concedido **ou** num subgrupo concedido. Exigir os dois
    excluiria registros cadastrados só no nível do grupo, sem subgrupo
    definido — que é o caso da maior parte do cadastro antigo.

    Devolve string vazia quando não há filtro a aplicar (master).
    """
    if not grupos and not subgrupos:
        return "", {}

    partes: list[str] = []
    params: dict = {}

    if grupos:
        partes.append(f"{alias}.{coluna_grupo} = ANY(:escopo_grupos)")
        params["escopo_grupos"] = grupos
    if subgrupos and coluna_subgrupo:
        partes.append(f"{alias}.{coluna_subgrupo} = ANY(:escopo_subgrupos)")
        params["escopo_subgrupos"] = subgrupos

    if not partes:
        return "", {}

    return f" AND ({' OR '.join(partes)})", params
