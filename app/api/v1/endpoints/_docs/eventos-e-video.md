# Eventos e vídeo — `events.py`, `video.py`, `bi.py` (lista)

Telas: `push-it-on-over/src/screens/Eventos.tsx` (Segurança › Eventos; mostra 40
e carrega +60) e `Videotelemetria.tsx`.

- `fleet_events` é gravada pelo `ss-worker-alarm-analyze`; aqui só leitura.
- `POST /events/{id}/acknowledge` **grava em produção** — não usar enquanto o
  banco for só leitura (a tela não chama).
- Vídeo: câmeras com estado de comunicação (offline após 20 min) e ocorrências
  DMS/ADAS/equipamento (`TIPOS_DMS`, `TIPOS_ADAS`, `TIPOS_EQUIPAMENTO`).
  Alarme de saúde da câmera ("calibração anormal", "baixa voltagem") fica
  separado do comportamento do motorista — não cobrar a pessoa errada.
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
