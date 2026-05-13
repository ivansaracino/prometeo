import os

try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from PIL import Image
    import pytesseract
    pytesseract.get_tesseract_version()
    OCR_SUPPORT = True
except Exception:
    OCR_SUPPORT = False

UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx'}

RUOLI = ['Tecnico', 'Amministrativo', 'Direzione', 'Risorse Umane']

PERMESSI = {
    'Tecnico':        ['tecnico', 'kpi', 'match_archivi'],
    'Amministrativo': ['amministrazione', 'banca', 'risorse_umane', 'fornitori', 'match_archivi'],
    'Direzione':      ['tecnico', 'kpi', 'amministrazione', 'banca', 'risorse_umane', 'owner',
                       'admin_utenti', 'fornitori', 'match_archivi', 'tesoreria'],
    'Risorse Umane':  ['risorse_umane'],
}

SEZIONI_LABEL = {
    'tecnico':         'Tecnico',
    'kpi':             'KPI',
    'amministrazione': 'Amministrazione',
    'banca':           'Banca',
    'risorse_umane':   'Risorse Umane',
    'owner':           'Responsabili',
    'admin_utenti':    'Gestione Utenti',
    'fornitori':       'Fornitori',
    'match_archivi':   'Match e Archivi',
    'tesoreria':       'Tesoreria',
}

SOGLIA_APPROVAZIONE = 5000.00
ALERT_SCADENZA_GIORNI = 30
