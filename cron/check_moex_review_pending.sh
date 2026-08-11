#!/bin/bash
# check_moex_review_pending.sh — вывод флага неоднозначных проблем для LLM-агента.
# Пустой stdout = разбор не нужен (агент молчит).
cat /home/user/.hermes/scripts/.moex_review_pending 2>/dev/null || true
