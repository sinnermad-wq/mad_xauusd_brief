@echo off
pushd "C:\Users\madwa\projects\daily-xauusd-bot"
"C:\Users\madwa\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" "C:\Users\madwa\projects\daily-xauusd-bot\scripts\generate_xauusd_refresh.py" --mode morning
popd