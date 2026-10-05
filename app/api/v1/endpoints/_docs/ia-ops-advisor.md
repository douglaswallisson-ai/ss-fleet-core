# IA Ops Advisor — `ai_fleet.py` (`/ai-fleet`)

Tela: `push-it-on-over/src/screens/IAFleetManager.tsx` + `screens/ai-fleet/PainelReal.tsx`
(menu IA Ops Advisor).

- Nenhuma regra de negócio no arquivo, de propósito: tudo vem das funções do
  banco `fleet_mvp.*` / `fleet_ai.*`, gravadas pelo worker `ss-worker-fleet-insights`.
  A mesma `kpis_periodo` alimenta a tela e o texto da IA (vault: indicadores-ai-ops-advisor).
- Acesso: só contas do acesso do usuário (SS/master vê todas). Janela máxima 92 dias;
  comparação padrão = a mesma janela 3 meses antes.
- O usuário de banco precisa de USAGE em `fleet_mvp` e `fleet_ai`; sem isso a rota responde 503 explicando.

**Não confundir** com a Selma (assistente de conversa, canto inferior direito):
ela ainda não tem backend — ver `push-it-on-over/src/components/ss/selma/CLAUDE.md`.
