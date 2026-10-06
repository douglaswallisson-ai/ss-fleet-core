# Eventos e vídeo — `events.py`, `video.py`, `bi.py` (lista)

Telas: `push-it-on-over/src/screens/Eventos.tsx` (Segurança › Eventos; mostra 40
e carrega +60) e `Videotelemetria.tsx`.

- `fleet_events` está **vazia** em produção (06/10/2026). Desde então:
  - `GET /events/` lê `mova.alarm_violation` + `mova.alarm` (todos os disparos do
    intervalo; `severity` pelo `level`: 3 CRITICAL, 2 WARNING, 1 INFO; "reconhecido"
    = visto ou tratado no Monitor da plataforma atual). `/events/alarmes` continua
    sendo o recorte do Monitor (só `notif_monitor`, alarme ativo, conta do usuário).
  - `GET /video/occurrences` lê `vcms.vcms_history`; nome do tipo por
    (type, device_model_id, source) em `vcms_alarm_type` — ver `app/core/camera.py`.
    O id sozinho erra o nome (ADAS "Veículo Muito Próximo" saía como "Fumando").
    DMS/ADAS pelo `alarm_source`; "tratada" = `verified` > 0. Sem veículo, fica de fora.
- `POST /events/{id}/acknowledge` responde **501** sem tocar no banco (os ids agora
  são de `alarm_violation`; a tratativa é feita no Monitor da plataforma atual).
- Vídeo: câmeras com estado de comunicação (offline após 20 min) e ocorrências
  DMS/ADAS/equipamento. Alarme de saúde da câmera (obstrução, imagem com exceção,
  óculos bloqueadores de IV) fica separado do comportamento do motorista — não
  cobrar a pessoa errada.
- A lista de eventos com local vem de `bi.py /eventos/lista` (`heatmap`).

## Vídeo ao vivo — `cameras.py` (05/10/2026)

Tela: `Videotelemetria.tsx` › Tempo real (`components/ss/video/AoVivoReal.tsx`). Cópia da
"Vídeos Online" da plataforma de câmeras (`plataforma_web_new`, `src/views/pages/cam/VideoOnline`).

- `GET /cameras/veiculos?group_id=` — vínculos ativos de `vcms.vcms_unit_device`, última posição
  (`mova.dev_status`) e status online consultado no proxy (cache 60 s). Proxy fora do ar vira
  `avisos`, não erro.
- `POST /cameras/ao-vivo {unit_id, canal}` — confere o grupo do usuário e devolve `{url, formato}`.
- Modelos: 122 JC450 → proxy JIMI (`JIMI_PROXY_URL`, URL :8881 trocada por `JIMI_STREAM_CDN`);
  156/157 G40 → proxy Hikvision (`HIKVISION_API_URL` + `HIKVISION_API_KEY`, pedir ao TI);
  114 MV03 não tem ao vivo (nem na plataforma de câmeras).
- Em 05/10/2026 o proxy JIMI respondia `Failed to contact orchestrator` (orquestrador recusando
  conexão): status e vídeo JIMI indisponíveis até o TI subir o serviço.
- Fora daqui de propósito: configuração ADAS/DMS e o comando `STATUS` (mudam/acionam o equipamento).
