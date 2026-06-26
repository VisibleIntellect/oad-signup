#!/bin/bash
# Double-click this file to start the sign-up app on a Mac.
cd "$(dirname "$0")"

echo "Setting up (first run takes a minute)..."
python3 -m venv venv 2>/dev/null
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ""
echo "Starting the sign-up app. Leave this window open while the event runs."
echo "To stop it, close this window or press Control-C."
echo ""
python3 app.py
