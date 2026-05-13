import os
import threading
import webbrowser
from app_factory import create_app

app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    def apri_browser():
        webbrowser.open(f'http://localhost:{port}')

    threading.Timer(1.5, apri_browser).start()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
