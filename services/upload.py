import os
from datetime import datetime
from werkzeug.utils import secure_filename
from config import UPLOAD_ROOT, ALLOWED_EXTENSIONS


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def salva_file(file, sottocartella):
    folder = os.path.join(UPLOAD_ROOT, sottocartella)
    os.makedirs(folder, exist_ok=True)
    filename = secure_filename(file.filename)
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S_')
    filename = ts + filename
    file.save(os.path.join(folder, filename))
    return os.path.join(sottocartella, filename)


def elimina_file(percorso):
    if percorso:
        full = os.path.join(UPLOAD_ROOT, percorso)
        if os.path.exists(full):
            os.remove(full)
