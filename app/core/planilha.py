"""Leitura de planilha enviada pelo usuário (.xlsx ou .csv), sem biblioteca externa.

O .xlsx é um zip de XMLs: lê a primeira aba, com os textos compartilhados. Basta para listas
simples (nome, endereço, empresa) — fórmulas viram o último valor calculado que o Excel gravou.
"""

import csv
import io
import re
import zipfile
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
MAX_LINHAS = 5000


class PlanilhaInvalida(ValueError):
    pass


def _coluna(ref: str) -> int:
    letras = re.match(r"[A-Z]+", ref or "")
    n = 0
    for c in letras.group(0) if letras else "A":
        n = n * 26 + (ord(c) - 64)
    return n - 1


def _xlsx(conteudo: bytes) -> list[list[str]]:
    try:
        z = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile as e:
        raise PlanilhaInvalida("O arquivo não é um Excel (.xlsx) válido.") from e
    nomes = set(z.namelist())
    textos: list[str] = []
    if "xl/sharedStrings.xml" in nomes:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            textos.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    # Primeira aba pela ordem do livro (não pelo nome do arquivo).
    alvo = "xl/worksheets/sheet1.xml"
    if "xl/workbook.xml" in nomes and "xl/_rels/workbook.xml.rels" in nomes:
        livro = ET.fromstring(z.read("xl/workbook.xml"))
        aba = livro.find("m:sheets/m:sheet", NS)
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        if aba is not None:
            rid = aba.get(NS_REL)
            for r in rels:
                if r.get("Id") == rid:
                    t = r.get("Target", "").lstrip("/")
                    alvo = t if t.startswith("xl/") else f"xl/{t}"
    if alvo not in nomes:
        raise PlanilhaInvalida("Não achei a primeira aba da planilha.")
    linhas: list[list[str]] = []
    for row in ET.fromstring(z.read(alvo)).iter(f"{{{NS['m']}}}row"):
        valores: dict[int, str] = {}
        for c in row.findall("m:c", NS):
            tipo = c.get("t")
            if tipo == "inlineStr":
                v = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
            else:
                el = c.find("m:v", NS)
                v = el.text if el is not None and el.text is not None else ""
                if tipo == "s" and v.isdigit() and int(v) < len(textos):
                    v = textos[int(v)]
            valores[_coluna(c.get("r", ""))] = v.strip()
        if valores:
            linhas.append([valores.get(i, "") for i in range(max(valores) + 1)])
        if len(linhas) > MAX_LINHAS:
            raise PlanilhaInvalida(f"A planilha passa de {MAX_LINHAS} linhas.")
    return linhas


def _csv(conteudo: bytes) -> list[list[str]]:
    for cod in ("utf-8-sig", "latin-1"):
        try:
            texto = conteudo.decode(cod)
            break
        except UnicodeDecodeError:
            continue
    amostra = texto[:4000]
    sep = ";" if amostra.count(";") >= amostra.count(",") else ","
    linhas = [[c.strip() for c in r] for r in csv.reader(io.StringIO(texto), delimiter=sep) if any(c.strip() for c in r)]
    if len(linhas) > MAX_LINHAS:
        raise PlanilhaInvalida(f"A planilha passa de {MAX_LINHAS} linhas.")
    return linhas


def ler(nome_arquivo: str, conteudo: bytes) -> list[list[str]]:
    """Linhas da planilha (a primeira é o cabeçalho)."""
    if conteudo[:2] == b"PK" or nome_arquivo.lower().endswith((".xlsx", ".xlsm")):
        linhas = _xlsx(conteudo)
    elif nome_arquivo.lower().endswith(".xls"):
        raise PlanilhaInvalida("Excel antigo (.xls) não é aceito: salve como .xlsx ou .csv.")
    else:
        linhas = _csv(conteudo)
    if len(linhas) < 2:
        raise PlanilhaInvalida("A planilha está vazia ou só tem o cabeçalho.")
    return linhas


def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    for a, b in (("áàâã", "a"), ("éê", "e"), ("í", "i"), ("óôõ", "o"), ("ú", "u"), ("ç", "c")):
        for ch in a:
            s = s.replace(ch, b)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


#: Nomes de coluna reconhecidos (já sem acento e em minúsculas).
COLUNAS = {
    "nome": ("nome", "funcionario", "colaborador", "passageiro", "nome completo", "nome do funcionario"),
    "endereco": ("endereco", "endereco completo", "logradouro", "rua", "endereco residencial"),
    "numero": ("numero", "n", "nº", "no", "num"),
    "bairro": ("bairro",),
    "cidade": ("cidade", "municipio"),
    "uf": ("uf", "estado"),
    "cep": ("cep",),
    "empresa": ("empresa", "cliente", "unidade", "contratante", "empresa contratante"),
}


def detectar_colunas(cabecalho: list[str]) -> dict[str, int]:
    """Posição de cada campo pelo nome da coluna (ou -1)."""
    norm = [_norm(c) for c in cabecalho]
    achado: dict[str, int] = {}
    for campo, nomes in COLUNAS.items():
        idx = next((i for i, c in enumerate(norm) if c in nomes), -1)
        if idx < 0:
            idx = next((i for i, c in enumerate(norm) if any(c.startswith(n) for n in nomes if len(n) > 3)), -1)
        achado[campo] = idx
    return achado
