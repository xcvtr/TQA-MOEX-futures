# Diagnostic Checkpoint — 2026-06-26

**Timestamp:** 2026-06-26 16:15:03
**Status:** ❌ FAILED — трейдер НЕ ЗАПУЩЕН

## Issues Found

1. NG: 168 signals on 2026-06-19 (max 15/day) — thresholds too loose
2. NG: 168 signals on 2026-06-22 (max 15/day) — thresholds too loose
3. NG: 161 signals on 2026-06-23 (max 15/day) — thresholds too loose
4. NG: 168 signals on 2026-06-24 (max 15/day) — thresholds too loose
5. NG: 168 signals on 2026-06-25 (max 15/day) — thresholds too loose
6. BR: 168 signals on 2026-06-19 (max 15/day) — thresholds too loose
7. BR: 168 signals on 2026-06-22 (max 15/day) — thresholds too loose
8. BR: 161 signals on 2026-06-23 (max 15/day) — thresholds too loose
9. BR: 168 signals on 2026-06-24 (max 15/day) — thresholds too loose
10. BR: 168 signals on 2026-06-25 (max 15/day) — thresholds too loose
11. Si: 168 signals on 2026-06-19 (max 15/day) — thresholds too loose
12. Si: 168 signals on 2026-06-22 (max 15/day) — thresholds too loose
13. Si: 161 signals on 2026-06-23 (max 15/day) — thresholds too loose
14. Si: 168 signals on 2026-06-24 (max 15/day) — thresholds too loose
15. Si: 168 signals on 2026-06-25 (max 15/day) — thresholds too loose
16. MXI: 168 signals on 2026-06-19 (max 15/day) — thresholds too loose
17. MXI: 168 signals on 2026-06-22 (max 15/day) — thresholds too loose
18. MXI: 161 signals on 2026-06-23 (max 15/day) — thresholds too loose
19. MXI: 168 signals on 2026-06-24 (max 15/day) — thresholds too loose
20. MXI: 168 signals on 2026-06-25 (max 15/day) — thresholds too loose
21. ALL 4 symbols have issues — possible systemic problem (API down?)


## Recommendation
- Проверить источник данных (AlgoPack API доступен?)
- Проверить кеш SQLite (не бит?)
- Если проблема в границах порогов — возможно, рынок изменился, нужен пересмотр Q
- После исправления — запустить paper trader повторно
