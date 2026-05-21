@echo off
:: checkStatus — runs every 30 minutes, asks before applying changes
:: Called by Windows Task Scheduler at 9:30am on weekdays

cd /d "C:\Users\m.balharith\Desktop\Claude code\Agents alpaca"

echo [%date% %time%] Starting checkStatus monitor... >> logs\scheduler.log
python checkStatus.py --loop 30 >> logs\checkStatus.log 2>&1
echo [%date% %time%] checkStatus exited. >> logs\scheduler.log
