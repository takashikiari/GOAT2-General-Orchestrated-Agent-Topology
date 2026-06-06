#!/bin/bash
cd ~/workspace/goat2
unset GROQ_API_KEY
unset OPENAI_API_KEY
unset DEEPSEEK_API_KEY
source .env
python3 -m supervisor.interfaces.telegram_bot
