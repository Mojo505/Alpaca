@echo off
:: checkStatus AUTO — runs every 30 minutes and applies adjustments automatically
:: Called by Windows Task Scheduler at 9:30am on weekdays

cd /d "C:\Users\m.balharith\Desktop\Claude code\Agents alpaca"

echo [%date% %time%] Starting checkStatus AUTO... >> logs\scheduler.log
python checkStatus.py --loop 30 --auto >> logs\checkStatus_auto.log 2>&1
echo [%date% %time%] checkStatus AUTO exited. >> logs\scheduler.log
