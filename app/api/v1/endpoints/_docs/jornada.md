# Jornada — `jornada.py` (`/jornada`)

Tela: `push-it-on-over/src/screens/jornada/JornadaReal.tsx` (Pessoas › Jornada,
escala e ponto). Decisão do PM: jornada pela telemetria é **conferência**, não
ponto; o ponto do motorista será tela/app próprio no futuro.

## De onde vem
- Telemetria: trechos de `con_telemetry` com o motorista identificado. Jornada = 1º ao último trecho do dia; direção = soma dos trechos; pausa = intervalo entre trechos.
- Diário de bordo (`driver_logbook`, eventos 47 login / 5 logout) quando o cliente usa (hoje a Quataí).

## Regras da lei (`REGRAS`, por operação; pesquisa de 02/10/2026 — confirmar com o jurídico antes de autuar)
- CLT 235-C (Lei 13.103/2015): 8 h + até 2 h extras (até 4 h com convenção); refeição ≥ 1 h quando a jornada passa de 6 h.
- CTB 67-C: no máximo 5h30 de direção ininterrupta; descanso de 30 min a cada 6 h (carga) / 4 h (passageiros), fracionável — as pausas são somadas até 30 min.
- Interjornada de 11 h **ininterruptas** (STF, ADI 5322, 30/06/2023); tempo de espera conta como jornada.
- Noturno 22 h–5 h. Pausa conta a partir de 10 min (`PAUSA_MIN`, SUPOSIÇÃO).
- Nova jornada após 6 h sem trecho (`DESCANSO_SEPARA_H`, SUPOSIÇÃO); jornada > 16 h = "a conferir" (provável cartão de outro motorista).

## Identificação (`GET /identificacao`)
Km sem motorista identificado e identificação presa (> 16 h sem descanso de 6 h).
Motorista "Não informado/identificado" (`CORINGA`) conta como sem identificação.
Achado FERTRAN set/2026: 87,5% dos km sem motorista.

## Gravação provisória
`data/jornada.sqlite`: escala planejada (dias, início, fim, linha) e
justificativas do espelho (`MOTIVOS`).
