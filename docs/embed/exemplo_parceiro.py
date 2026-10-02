"""
Exemplo do lado do PARCEIRO (ex.: Citatti): gera o ticket e abre a tela da
plataforma SS dentro de um iframe.

Uso (teste local):
    EMBED_SEGREDO=... EMBED_PARCEIRO=citatti EMBED_USUARIO=<login SS> \
    PLATAFORMA=http://localhost:8080 python docs/embed/exemplo_parceiro.py
e abra http://localhost:8090

Em produção o ticket deve ser gerado no SERVIDOR do parceiro (o segredo nunca
vai para o navegador). Cada ticket vale 60 s e só pode ser usado uma vez.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

SEGREDO = os.environ["EMBED_SEGREDO"]
PARCEIRO = os.environ.get("EMBED_PARCEIRO", "citatti")
USUARIO = os.environ["EMBED_USUARIO"]
PLATAFORMA = os.environ.get("PLATAFORMA", "http://localhost:8080").rstrip("/")


def gerar_ticket(usuario: str) -> str:
    corpo = json.dumps({"p": PARCEIRO, "u": usuario, "t": int(time.time()), "n": secrets.token_hex(12)})
    payload = base64.urlsafe_b64encode(corpo.encode()).decode().rstrip("=")
    assinatura = hmac.new(SEGREDO.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{assinatura}"


TELAS = [
    ("Início", "/app"), ("Mapa ao vivo", "/app/mapa"), ("Percurso do dia", "/app/frota/tracking"),
    ("Veículos", "/app/veiculos"), ("Motoristas", "/app/motoristas"), ("Eventos", "/app/eventos"),
    ("Gerencial", "/app/gerencial"), ("Relatórios", "/app/relatorios"), ("Escala de viagem", "/app/escala-viagem"),
    ("Emissão de CO₂", "/app/co2"), ("Suporte", "/app/suporte"),
]

PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Sistema do parceiro (exemplo)</title>
<style>body{margin:0;font-family:system-ui;display:grid;grid-template-columns:220px 1fr;height:100vh}
nav{background:#0a4d8c;color:#fff;padding:16px}nav b{display:block;margin-bottom:16px;font-size:18px}
nav a{display:block;color:#fff;text-decoration:none;padding:8px;border-radius:6px}nav a:hover{background:#ffffff22}
iframe{border:0;width:100%%;height:100%%}</style></head><body>
<nav><b>Parceiro</b>%s</nav><iframe id="ss" src="%s" allow="clipboard-write"></iframe>
<script>
window.addEventListener("message", e => { if (e.data && e.data.origem === "ss-plataforma") console.log("SS →", e.data); });
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        tela = q.get("tela", ["/app"])[0]
        src = f"{PLATAFORMA}/embed?ticket={quote(gerar_ticket(USUARIO))}&tela={quote(tela)}"
        # Opcional: cores e logo na hora (por cima da marca cadastrada para o parceiro).
        for k in ("cor", "destaque", "logo", "menu"):
            if k in q:
                src += f"&{k}={quote(q[k][0])}"
        menu = "".join(f'<a href="/?tela={quote(t)}">{n}</a>' for n, t in TELAS)
        corpo = (PAGINA % (menu, src)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8090), H).serve_forever()
