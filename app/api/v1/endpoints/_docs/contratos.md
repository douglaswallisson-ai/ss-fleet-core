# Contratos — `contratos.py`

Tela: `push-it-on-over/src/screens/contratos/ContratosReal.tsx` (Console › Contratos).

Regras do PM (06/10/2026):
- todo cliente (grupo) tem um contrato; **grupo novo só nasce pelo "Novo contrato"**
  (`POST /cadastros/empresa` responde 409);
- clientes que já existiam: contrato pré-preenchido, **sem campo obrigatório**;
  "a completar" é só informativo;
- **nunca encerra sozinho**: depois do fim aparece "Vencido"; encerrar/suspender
  é manual, com motivo (`POST /contratos/{id}/status`);
- contrato novo exige: nome do grupo (único), razão social, CNPJ (dígitos
  conferidos), segmento, início, fim ≥ início, veículos ≥ 1, parcela > 0,
  contato e e-mail.

Aditivos de veículos (`POST /contratos/{id}/aditivos`, PM 06/10/2026):
- todo contrato tem número fixo do sistema `CT-00042` (do id; não muda);
- aditivo = `CT-00042-AD01`, `AD02`…, gravado na criação junto com o número do pai;
- inclusão soma e retirada subtrai veículos e parcela; retirada não pode passar da frota;
- não se apaga aditivo: cancela com motivo (sai dos totais, o número não é reaproveitado);
- contrato encerrado não recebe aditivo.

Pré-preenchimento: `mova."group"` + `cliente_financeiro_vigencia` **sem as linhas
`registrado_por = 'teste'`** (170 de 199 eram fictícias em 06/10/2026) + veículos
ativos. O sincronismo (a cada 5 min, na listagem) só cria o que falta; nunca
sobrescreve o que foi editado.

Pendente (decisão do PM): o ROI da Início ainda lê `cliente_financeiro_vigencia`;
o grupo do contrato novo fica "a criar" até a engenharia liberar a gravação.
Testes: `tests/test_regras_contrato.py`.
