import os
from app_factory import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # NOTA: il browser viene aperto dal launcher (launcher.ps1 / avvia.sh)
    # quando il server è effettivamente pronto. Qui NON apriamo nulla per
    # evitare la doppia finestra. Se vuoi forzare l'apertura automatica
    # eseguendo direttamente `python run.py`, imposta AUTO_OPEN_BROWSER=1.
    if os.environ.get('AUTO_OPEN_BROWSER') == '1':
        import threading, webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
