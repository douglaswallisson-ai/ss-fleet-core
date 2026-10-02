# Telas da SS dentro do seu sistema (modo embutido)

Qualquer tela da plataforma SS pode ser aberta dentro do seu sistema, já logada, sem o menu e a marca da SS e com as suas cores e a sua logo.

## Como funciona

1. O **seu servidor** gera um *ticket* assinado com o segredo que a SS fornece. O ticket vale **60 segundos** e só pode ser usado **uma vez**.
2. A sua página abre um `iframe`:

   ```
   https://<plataforma-ss>/embed?ticket=<ticket>&tela=/app/mapa
   ```

3. A plataforma valida o ticket, entra com o usuário indicado e abre a tela.

O segredo **nunca** deve ir para o navegador. Gere o ticket no backend, a cada abertura do iframe.

## Ticket

`<payload>.<assinatura>`

- `payload` = base64url (sem `=`) de um JSON:

  | campo | o que é |
  |---|---|
  | `p` | id do parceiro (fornecido pela SS) |
  | `u` | login, e-mail ou id do usuário na plataforma SS |
  | `t` | data e hora de emissão (unix, segundos) |
  | `n` | texto aleatório único (mínimo 8 caracteres) |

- `assinatura` = HMAC-SHA256 do `payload` com o seu segredo, em hexadecimal.

Exemplo em Python: [`exemplo_parceiro.py`](exemplo_parceiro.py).

```js
// Node.js
const crypto = require("crypto");
function ticket(parceiro, segredo, usuario) {
  const corpo = JSON.stringify({ p: parceiro, u: usuario, t: Math.floor(Date.now() / 1000), n: crypto.randomBytes(12).toString("hex") });
  const payload = Buffer.from(corpo).toString("base64url");
  return `${payload}.${crypto.createHmac("sha256", segredo).update(payload).digest("hex")}`;
}
```

O usuário precisa estar ativo na SS e pertencer às contas liberadas para o seu sistema. Ele vê exatamente o que vê na plataforma SS: mesmos grupos e mesmas permissões.

## Parâmetros do endereço

| parâmetro | exemplo | efeito |
|---|---|---|
| `tela` | `/app/gerencial` | tela que abre (padrão: `/app`, a Início) |
| `cor` | `0A4D8C` | cor principal (hex, sem #) |
| `destaque` | `F2A900` | cor de destaque |
| `logo` | `https://seusite/logo.svg` | logo na barra de cima (somente https) |
| `menu` | `1` | mostra o menu lateral da SS (padrão: escondido) |

Cores e logo também podem ficar cadastradas na SS para o seu parceiro; os parâmetros valem por cima delas.

## Telas

Todas as telas da plataforma, pelo caminho `/app/...`. Principais:

| Tela | Caminho |
|---|---|
| Início | `/app` |
| Mapa ao vivo | `/app/mapa` |
| Percurso do dia | `/app/frota/tracking` |
| Veículos | `/app/veiculos` |
| Motoristas | `/app/motoristas` |
| Eventos | `/app/eventos` |
| Gerencial | `/app/gerencial` |
| Relatórios | `/app/relatorios` |
| Escala de viagem | `/app/escala-viagem` |
| Painel sinótico | `/app/operacao/sinotico` |
| Gestão de viagens | `/app/operacao/viagens` |
| Emissão de CO₂ | `/app/co2` |
| Cadastros | `/app/cadastros/<cerca, usuarios, unidades, ...>` |
| Suporte | `/app/suporte` |

## Conversa entre as páginas (opcional)

A plataforma envia ao seu site (`window.postMessage`, somente para a sua origem cadastrada):

```js
{ origem: "ss-plataforma", tipo: "ss:pagina", caminho: "/app/mapa", titulo: "Mapa ao vivo" }
{ origem: "ss-plataforma", tipo: "ss:altura", altura: 1840 }   // para ajustar a altura do iframe
```

E aceita do seu site:

```js
iframe.contentWindow.postMessage({ tipo: "ss:navegar", caminho: "/app/gerencial" }, "https://<plataforma-ss>");
iframe.contentWindow.postMessage({ tipo: "ss:marca", cor: "#0A4D8C", logo: "https://..." }, "https://<plataforma-ss>");
```

Com `ss:navegar` você troca de tela sem gerar ticket novo.

## Segurança

- A tela só abre **dentro de um iframe** numa das origens cadastradas para o seu parceiro (ex.: `https://app.seusite.com.br`).
- Ticket vencido, reutilizado, com assinatura errada ou de usuário fora das contas liberadas é recusado.
- Quando a sessão expira, a tela pede para recarregar pelo seu sistema. Basta gerar um ticket novo.

## O que a SS cadastra para você

- id do parceiro e segredo;
- contas liberadas;
- origens do seu site;
- cores e logo padrão (opcional).
