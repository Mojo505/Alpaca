@echo off
:: Orchestrator — runs once at 9:00am to scan, select and place orders
:: Called by Windows Task Scheduler at market open

cd /d "C:\Users\m.balharith\Desktop\Claude code\Agents alpaca"

echo [%date% %time%] Starting orchestrator... >> logs\scheduler.log
python orchestrator.py >> logs\orchestrator.log 2>&1
echo [%date% %time%] Orchestrator complete. >> logs\scheduler.log
