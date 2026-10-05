# Suporte — `suporte.py` (`/suporte`)

Tela: `push-it-on-over/src/screens/Suporte.tsx` (artigos de ajuda + chamados;
a Selma aparece como personagem, mas as respostas dos artigos são texto fixo de
`lib/suporte-faq.ts`, não IA).

Mesma lógica do sistema antigo (`zendeskcontroller-class.php` + widget `zendesk`):
- assunto = "Serviço" de uma lista fixa (`SERVICOS`, a mesma do antigo, para as visões do Zendesk continuarem valendo);
- solicitante = usuário logado (e-mail da sessão, nunca digitado);
- anexo opcional (imagem, PDF, DOCX, XLSX, CSV, texto; ≤ 10 MB) sobe antes em `/api/v2/uploads.json`;
- "Meus chamados" = últimos 3 meses; detalhe confere que o chamado é do usuário.
- Urgência em palavras simples (`PRIORIDADES`).

Credenciais só por variável de ambiente (`ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`,
`ZENDESK_API_TOKEN`). Sem elas a integração fica desligada: o chamado vai para
`data/suporte.sqlite` e a tela avisa. **Nunca copiar o token que existe no código antigo.**
