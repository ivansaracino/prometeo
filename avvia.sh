#!/bin/bash
echo "============================================"
echo "   Gestionale Aziendale - Avvio in corso..."
echo "============================================"
echo ""

# Installa i pacchetti necessari
pip install -r requirements.txt --quiet

echo ""
echo "Avvio server... Il browser si aprirà automaticamente."
echo "Per fermare l'app premi CTRL+C"
echo ""

python app.py
