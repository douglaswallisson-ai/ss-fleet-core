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
