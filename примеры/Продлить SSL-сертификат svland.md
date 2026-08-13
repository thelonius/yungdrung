---
schema: 1
type: task
title: Продлить SSL-сертификат svland
created: 2026-08-01
status: закрыта
current_step: null
control_date: null
progress: 2/2
stalled: 0
tags:
- инфра
steps:
- id: 1
  title: Заказать сертификат у регистратора
  status: done
  control_date: 2026-08-01
  completed_date: 2026-08-01
  log:
  - date: 2026-08-01
    event: done
- id: 2
  title: Прописать в Caddyfile и перезапустить
  status: done
  control_date: 2026-08-02
  completed_date: 2026-08-02
  log:
  - date: 2026-08-02
    event: done
---

<!-- шаги: пишет движок, править руками не нужно -->
**Шаги**

✓ **1.** Заказать сертификат у регистратора — сделан 01.08.2026
✓ **2.** Прописать в Caddyfile и перезапустить — сделан 02.08.2026

<!-- /шаги -->

Простейший случай: два шага, оба закрыты без переносов. Задача целиком `done`,
потому что все шаги `done` или `skipped`.
