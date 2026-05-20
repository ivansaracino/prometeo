import os
import re
import io
import json
import random
import threading
import webbrowser
from datetime import datetime, date, timedelta
from flask import Flask, render_template, redirect, url_for, request, session, flash, send_from_directory, jsonify, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps

try:
    import pdfplumber
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from PIL import Image
    import pytesseract
    # Tesseract 5 installed via nix; ita+eng language pack
    _tess_ver = pytesseract.get_tesseract_version()
    OCR_SUPPORT = True
except Exception:
    OCR_SUPPORT = False

app = Flask(__name__)
app.jinja_env.globals['chr'] = chr  # rende chr() disponibile nei template
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-prod')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///webapp.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024  # 32 MB

UPLOAD_ROOT = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx'}

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Devi effettuare il login per accedere a questa pagina.'
login_manager.login_message_category = 'warning'

RUOLI = ['Tecnico', 'Amministrativo', 'Direzione', 'Risorse Umane']

PERMESSI = {
    'Tecnico':        ['tecnico', 'kpi','match_archivi'],
    'Amministrativo': ['amministrazione', 'banca', 'risorse_umane', 'fornitori', 'match_archivi'],
    'Direzione':      ['tecnico', 'kpi', 'amministrazione', 'banca', 'risorse_umane', 'owner', 'admin_utenti', 'fornitori', 'match_archivi', 'tesoreria'],
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


# ── Helpers upload ────────────────────────────────────────────────────────────

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


# ── Models ────────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200))               # nome visualizzato, usato come owner
    email = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    ruolo = db.Column(db.String(50), nullable=False)
    attivo = db.Column(db.Boolean, default=True, nullable=False)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def ha_accesso(self, sezione):
        return sezione in PERMESSI.get(self.ruolo, [])

    def sezioni_accessibili(self):
        return PERMESSI.get(self.ruolo, [])

    def is_active(self):
        return self.attivo


class Cliente(db.Model):
    __tablename__ = 'clienti'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer)                   # codice progressivo cliente
    nome = db.Column(db.String(200), nullable=False)
    partita_iva = db.Column(db.String(30))
    cig = db.Column(db.String(100))                  # CIG (opzionale – ente pubblico)
    cup = db.Column(db.String(100))                  # CUP (opzionale – ente pubblico)
    owner = db.Column(db.String(200))
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    commesse = db.relationship('Commessa', backref='cliente', lazy=True, cascade='all, delete-orphan')
    ordini = db.relationship('Ordine', backref='cliente', lazy=True, cascade='all, delete-orphan')


class Commessa(db.Model):
    __tablename__ = 'commesse'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=False)
    numero = db.Column(db.Integer, nullable=False, default=1)
    nome = db.Column(db.String(200), nullable=False)
    luogo = db.Column(db.String(200))
    via = db.Column(db.String(200))
    owner = db.Column(db.String(200))
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    costi = db.relationship('CostoFornitore', backref='commessa', lazy=True, cascade='all, delete')
    fatture = db.relationship('Fattura', backref='commessa_obj', lazy=True)

    @property
    def codice(self):
        """Restituisce il codice completo es. C001-1"""
        num_cl = (self.cliente.numero if self.cliente and self.cliente.numero else
                  (self.cliente.id if self.cliente else 0))
        return f'C{num_cl:03d}-{self.numero}'


class CostoFornitore(db.Model):
    __tablename__ = 'costi_fornitori'
    id = db.Column(db.Integer, primary_key=True)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    commessa_suggerita_id = db.Column(db.Integer, nullable=True)  # FK logica – non enforced
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=True)
    categoria = db.Column(db.String(50), nullable=False)
    sottocategoria = db.Column(db.String(20))      # 'ore' / 'pasti' / 'km'  (solo personale)
    descrizione = db.Column(db.String(300), nullable=False)
    quantita = db.Column(db.Float)                 # ore lavorate o km percorsi
    valore_unitario = db.Column(db.Float)          # €/ora o €/km
    importo = db.Column(db.Float, nullable=False, default=0.0)
    stato = db.Column(db.String(20), default='approvato')  # 'pending'|'approvato'|'rifiutato'
    data = db.Column(db.Date, default=datetime.utcnow)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)


class Ordine(db.Model):
    __tablename__ = 'ordini'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=False)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    identificativo = db.Column(db.String(50))        # es. C001-1 (num_cliente - num_commessa)
    azienda = db.Column(db.String(200))
    piva = db.Column(db.String(50))
    rif_ordine = db.Column(db.String(100))
    dati_cantiere = db.Column(db.String(300))
    indirizzo = db.Column(db.String(300))
    rif_cantiere = db.Column(db.String(100))
    note = db.Column(db.Text)
    cig  = db.Column(db.String(200))
    cup  = db.Column(db.String(200))
    totale = db.Column(db.Float, default=0.0)
    stato = db.Column(db.String(30), default='bozza')
    creato_da = db.Column(db.Integer, db.ForeignKey('users.id'))
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    righe = db.relationship('RigaOrdine', backref='ordine', lazy=True, cascade='all, delete-orphan', order_by='RigaOrdine.numero')


class RigaOrdine(db.Model):
    __tablename__ = 'righe_ordine'
    id = db.Column(db.Integer, primary_key=True)
    ordine_id = db.Column(db.Integer, db.ForeignKey('ordini.id'), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    descrizione = db.Column(db.String(300))
    qta = db.Column(db.Float, default=0.0)
    unita_misura = db.Column(db.String(30))
    prezzo_unitario = db.Column(db.Float, default=0.0)
    totale = db.Column(db.Float, default=0.0)


class Fattura(db.Model):
    __tablename__ = 'fatture'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(10), nullable=False)       # 'attiva' / 'passiva'
    sottotipo = db.Column(db.String(20))                  # 'subappalto' / 'materiali' (solo passiva)
    numero = db.Column(db.String(50))
    importo = db.Column(db.Float, default=0.0)
    cliente_fornitore = db.Column(db.String(200))
    data_emissione = db.Column(db.Date)
    data_scadenza = db.Column(db.Date)
    pagata = db.Column(db.Boolean, default=False)
    note = db.Column(db.Text)
    file_path = db.Column(db.String(500))
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=True)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    # Flusso approvazione tecnico
    stato_approvazione = db.Column(db.String(20), default='approvato')  # 'pending'|'approvato'|'rifiutato'
    codice_cliente_ref  = db.Column(db.String(20))
    codice_commessa_ref = db.Column(db.String(20))
    tecnico_id          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    motivo_rifiuto      = db.Column(db.Text, nullable=True)
    partita_iva_fornitore = db.Column(db.String(30), nullable=True)
    # Campi fattura attiva strutturata
    piva_cliente      = db.Column(db.String(30),  nullable=True)
    mia_piva          = db.Column(db.String(30),  nullable=True)
    indirizzo_cliente = db.Column(db.String(500), nullable=True)
    pec_cliente       = db.Column(db.String(200), nullable=True)
    cup               = db.Column(db.String(50),  nullable=True)
    cig               = db.Column(db.String(50),  nullable=True)
    aliquota_iva      = db.Column(db.Float,       nullable=True)
    importo_netto     = db.Column(db.Float,       nullable=True)


class RigaFatturaAttiva(db.Model):
    __tablename__ = 'righe_fattura_attiva'
    id              = db.Column(db.Integer, primary_key=True)
    fattura_id      = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=False)
    articolo        = db.Column(db.String(100), nullable=True)
    descrizione     = db.Column(db.Text,        nullable=False)
    quantita        = db.Column(db.Float,       nullable=True)
    unita_misura    = db.Column(db.String(20),  nullable=True)
    prezzo_unitario = db.Column(db.Float,       nullable=True)
    importo_netto   = db.Column(db.Float,       nullable=True)
    fattura         = db.relationship('Fattura', backref='righe_attiva', lazy=True)


class AppConfig(db.Model):
    """Configurazione chiave-valore persistente dell'applicazione."""
    __tablename__ = 'app_config'
    id    = db.Column(db.Integer, primary_key=True)
    chiave = db.Column(db.String(100), unique=True, nullable=False)
    valore  = db.Column(db.Text)

    @staticmethod
    def get(chiave, default=None):
        r = AppConfig.query.filter_by(chiave=chiave).first()
        return r.valore if r else default

    @staticmethod
    def set(chiave, valore):
        r = AppConfig.query.filter_by(chiave=chiave).first()
        if r:
            r.valore = valore
        else:
            db.session.add(AppConfig(chiave=chiave, valore=valore))
        db.session.commit()


class DDT(db.Model):
    """Documento di Trasporto — abbinato a fatture passive di materiali."""
    __tablename__ = 'ddt'
    id = db.Column(db.Integer, primary_key=True)
    numero             = db.Column(db.String(50))
    data               = db.Column(db.Date)
    fornitore          = db.Column(db.String(200))
    partita_iva        = db.Column(db.String(30),  nullable=True)   # P.IVA fornitore DDT
    codice_cliente     = db.Column(db.String(20))
    codice_commessa    = db.Column(db.String(20))
    importo            = db.Column(db.Float, default=0.0)
    descrizione        = db.Column(db.Text)
    indirizzo_cantiere = db.Column(db.String(500), nullable=True)   # estratto dal PDF
    # tecnico responsabile assegnato
    tecnico_id         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    motivo_rifiuto     = db.Column(db.Text, nullable=True)
    # matching / approvazione
    commessa_id        = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    fattura_id         = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=True)
    stato              = db.Column(db.String(30), default='non_matchato')
    # 'in_revisione'|'in_attesa_tecnico'|'approvato_tecnico'|'rifiutato_tecnico'
    # 'abbinato'|'non_matchato'|'importo_anomalo'
    note_anomalia      = db.Column(db.Text)
    file_path          = db.Column(db.String(500), nullable=True)
    creato_il          = db.Column(db.DateTime, default=datetime.utcnow)
    creato_da          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    # relazioni
    commessa = db.relationship('Commessa', backref='ddt', lazy=True, foreign_keys=[commessa_id])
    fattura  = db.relationship('Fattura',  backref='ddt', lazy=True, foreign_keys=[fattura_id])
    tecnico  = db.relationship('User', backref='ddt_assegnati', lazy=True, foreign_keys=[tecnico_id])



class RigaDDT(db.Model):
    """Riga estratta dal PDF di un DDT (bolla materiali).
    Ogni riga può appartenere a una commessa diversa.
    Usata per calcolare i costi materiali per commessa."""
    __tablename__ = 'righe_ddt'
    id               = db.Column(db.Integer, primary_key=True)
    ddt_id           = db.Column(db.Integer, db.ForeignKey('ddt.id'), nullable=False)
    commessa_id      = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    codice_commessa  = db.Column(db.String(30),  nullable=True)  # grezzo dal form
    descrizione      = db.Column(db.Text,        nullable=False)
    quantita         = db.Column(db.Float,       nullable=True)
    unita_misura     = db.Column(db.String(20),  nullable=True)
    prezzo_unitario  = db.Column(db.Float,       nullable=True)
    importo          = db.Column(db.Float,       nullable=True)  # qty × pu
    ddt              = db.relationship('DDT', backref='righe', lazy=True)
    commessa         = db.relationship('Commessa', backref='righe_ddt', lazy=True)

    def calc_importo(self):
        if self.quantita and self.prezzo_unitario:
            return round(self.quantita * self.prezzo_unitario, 2)
        return self.importo or 0.0


def _tecnico_per_commessa(commessa):
    """Trova il tecnico responsabile (owner del cliente) per una commessa."""
    if not commessa or not commessa.cliente:
        return None
    owner_nome = commessa.cliente.owner
    if not owner_nome:
        return None
    return User.query.filter(
        User.nome == owner_nome,
        User.ruolo.in_(['Tecnico', 'Direzione'])
    ).first()


def _estrai_indirizzo_cantiere(testo: str) -> str:
    """Prova a estrarre l'indirizzo/luogo del cantiere dal testo del DDT."""
    patterns = [
        r'(?:cantiere|c/o)[:\s]+([^\n]{5,120})',
        r'(?:luogo\s+di\s+consegna|luogo\s+consegna)[:\s]+([^\n]{5,120})',
        r'(?:indirizzo\s+cantiere|destino\s+merce)[:\s]+([^\n]{5,120})',
        r'(?:consegnare\s+(?:presso|a)|spedire\s+a)[:\s]+([^\n]{5,120})',
    ]
    for p in patterns:
        m = re.search(p, testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:300]
    return ''


def _commessa_per_indirizzo(indirizzo: str):
    """Tenta di trovare la commessa più probabile in base all'indirizzo del cantiere.
    Ritorna (commessa|None, score)."""
    if not indirizzo or len(indirizzo) < 5:
        return None, 0
    parole = [p for p in re.split(r'[\s,;.]+', indirizzo.upper()) if len(p) > 3]
    if not parole:
        return None, 0
    scores = {}
    for commessa in Commessa.query.all():
        testo_comm = ' '.join(filter(None, [
            commessa.via or '', commessa.luogo or ''
        ])).upper()
        if testo_comm:
            s = sum(1 for p in parole if p in testo_comm)
            if s > 0:
                scores[commessa] = s
    # Cerca anche negli ordini
    for ordine in Ordine.query.filter(Ordine.commessa_id.isnot(None)).all():
        campi = ' '.join(filter(None, [
            ordine.indirizzo or '', ordine.dati_cantiere or '', ordine.rif_cantiere or ''
        ])).upper()
        if campi:
            s = sum(1 for p in parole if p in campi)
            if s > 0 and ordine.commessa_id:
                c = db.session.get(Commessa, ordine.commessa_id)
                if c:
                    scores[c] = scores.get(c, 0) + s
    if not scores:
        return None, 0
    best = max(scores, key=scores.get)
    return best, scores[best]


def _cerca_commessa_nel_testo_pdf(testo: str):
    """Cerca il codice commessa direttamente nel testo estratto dal PDF.

    Priorità:
      1. Codice completo "C001-3" / "C001/3" (formato nativo del gestionale)
      2. Pattern con parola chiave ("rif commessa C001-3", "vs. rif. 001/3")
      3. Codici separati (cliente e commessa su righe diverse)

    Ritorna (commessa|None, cod_cliente_str, cod_commessa_str, metodo_str).
    """
    if not testo:
        return None, '', '', ''

    cod_cl, cod_cm, metodo = '', '', ''

    # ── 1. Formato completo "C001-3" o "C001/3" ──────────────────────
    # Questo è il formato usato internamente dal gestionale → massima affidabilità
    m = re.search(r'\bC0*(\d{1,4})[-/](\d{1,4})\b', testo, re.IGNORECASE)
    if m:
        cod_cl, cod_cm, metodo = m.group(1), m.group(2), 'codice completo (C{cl}-{cm})'

    # ── 2. Pattern con etichetta + codice completo ────────────────────
    if not (cod_cl and cod_cm):
        m = re.search(
            r'(?:rif(?:erimento)?\.?\s*(?:cantiere|commessa|lavori|progetto)?'
            r'|vs\.?\s*rif\.?|n[°º°]?\s*commessa|commessa\s*n[°º°]?'
            r'|cod(?:ice)?\s*commessa|progetto)[:\s.#]*'
            r'C?0*(\d{1,4})[-/](\d{1,4})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cl, cod_cm = m.group(1), m.group(2)
            metodo = 'riferimento commessa (etichetta + codice)'

    # ── 3. Codici separati: cliente + commessa su campi diversi ───────
    if not cod_cl:
        m = re.search(
            r'(?:cli(?:ente)?|cod(?:ice)?\s*cli(?:ente)?)[:\s]+C?0*(\d{1,5})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cl = m.group(1)
    if not cod_cm:
        m = re.search(
            r'(?:com(?:messa)?|cod(?:ice)?\s*com(?:messa)?'
            r'|n[°º]?\s*com(?:messa)?|n[°º]?\s*lavoro)[:\s]+0*(\d{1,5})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cm = m.group(1)
    if cod_cl and cod_cm and not metodo:
        metodo = 'codici separati (cliente + commessa)'

    # ── Lookup in DB ──────────────────────────────────────────────────
    if not (cod_cl and cod_cm):
        return None, cod_cl, cod_cm, metodo
    try:
        num_cl = int(cod_cl)
        num_cm = int(cod_cm)
        cli = Cliente.query.filter_by(numero=num_cl).first()
        if cli:
            commessa = Commessa.query.filter_by(
                cliente_id=cli.id, numero=num_cm
            ).first()
            if commessa:
                return commessa, cod_cl, cod_cm, metodo
    except (ValueError, TypeError):
        pass

    return None, cod_cl, cod_cm, metodo


def _match_ddt(codice_cliente_raw: str, codice_commessa_raw: str, importo: float):
    """Tenta di abbinare il DDT a una fattura passiva di materiali.
    Ritorna: (commessa|None, fattura|None, stato, note_anomalia)
    stati: 'abbinato' | 'importo_anomalo' | 'non_matchato'
    """
    # ── Parsing codice cliente ───────────────────────────────────────
    raw_cl = (codice_cliente_raw or '').strip().upper().lstrip('C').lstrip('0') or '0'
    try:
        num_cl = int(raw_cl)
    except ValueError:
        return None, None, 'non_matchato', f'Codice cliente non valido: "{codice_cliente_raw}"'

    cliente = Cliente.query.filter_by(numero=num_cl).first()
    if not cliente:
        return None, None, 'non_matchato', f'Nessun cliente con codice C{num_cl:03d}'

    # ── Parsing codice commessa ──────────────────────────────────────
    try:
        num_comm = int((codice_commessa_raw or '').strip())
    except ValueError:
        return None, None, 'non_matchato', f'Codice commessa non valido: "{codice_commessa_raw}"'

    commessa = Commessa.query.filter_by(cliente_id=cliente.id, numero=num_comm).first()
    if not commessa:
        return None, None, 'non_matchato', \
            f'Nessuna commessa N°{num_comm} per {cliente.nome} (C{num_cl:03d})'

    # ── Cerca fatture passive materiali per questa commessa ──────────
    fatture_mat = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali', commessa_id=commessa.id
    ).all()

    if not fatture_mat:
        return commessa, None, 'non_matchato', \
            f'Nessuna fattura materiali per commessa N°{num_comm} di {cliente.nome}'

    tot_fatture = sum(f.importo or 0 for f in fatture_mat)
    # Fattura di riferimento = quella con importo più alto (o prima disponibile)
    fattura_ref = max(fatture_mat, key=lambda f: f.importo or 0)

    if importo > tot_fatture:
        return commessa, fattura_ref, 'importo_anomalo', \
            f'DDT € {importo:,.2f} > fatture materiali € {tot_fatture:,.2f} (Δ € {importo - tot_fatture:,.2f})'

    return commessa, fattura_ref, 'abbinato', None


class Dipendente(db.Model):
    __tablename__ = 'dipendenti'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cognome = db.Column(db.String(100), nullable=False)
    costo_orario = db.Column(db.Float, default=0.0)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    tutti_documenti = db.relationship('DocumentoDipendente', backref='dipendente', lazy=True, cascade='all, delete-orphan')


class DocumentoDipendente(db.Model):
    __tablename__ = 'documenti_dipendenti'
    id = db.Column(db.Integer, primary_key=True)
    dipendente_id = db.Column(db.Integer, db.ForeignKey('dipendenti.id'), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    nome_file = db.Column(db.String(400))
    tipo = db.Column(db.String(20), nullable=False)       # 'documento' / 'corso'
    data_scadenza = db.Column(db.Date)                    # solo per corsi
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)


class NodoOrganico(db.Model):
    __tablename__ = 'nodi_organico'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    posizione = db.Column(db.String(200))
    parent_id = db.Column(db.Integer, db.ForeignKey('nodi_organico.id'), nullable=True)
    soglia_approvazione = db.Column(db.Float, nullable=True)
    colore = db.Column(db.String(20), default='blue')
    figli = db.relationship('NodoOrganico',
                            backref=db.backref('parent', remote_side=[id]),
                            lazy='select')


class Fornitore(db.Model):
    """Anagrafica fornitori — per matching DDT ↔ fatture passive materiali."""
    __tablename__ = 'fornitori'
    id             = db.Column(db.Integer, primary_key=True)
    nome           = db.Column(db.String(200), nullable=False)
    partita_iva    = db.Column(db.String(30),  nullable=True)
    codice_fiscale = db.Column(db.String(20),  nullable=True)
    indirizzo      = db.Column(db.String(300), nullable=True)
    email          = db.Column(db.String(200), nullable=True)
    telefono       = db.Column(db.String(50),  nullable=True)
    note           = db.Column(db.Text,        nullable=True)
    creato_il      = db.Column(db.DateTime, default=datetime.utcnow)
    creato_da      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)


class AbbinatoDdtFattura(db.Model):
    """Abbinamento DDT (bolla) ↔ Fattura passiva materiali, per P.IVA fornitore + commessa."""
    __tablename__ = 'abbinati_ddt_fattura'
    id           = db.Column(db.Integer, primary_key=True)
    ddt_id       = db.Column(db.Integer, db.ForeignKey('ddt.id'),       nullable=False)
    fattura_id   = db.Column(db.Integer, db.ForeignKey('fatture.id'),   nullable=False)
    commessa_id  = db.Column(db.Integer, db.ForeignKey('commesse.id'),  nullable=True)
    fornitore_id = db.Column(db.Integer, db.ForeignKey('fornitori.id'), nullable=True)
    score_desc   = db.Column(db.Float,   default=0.0)
    archivio_zip = db.Column(db.String(500), nullable=True)
    abbinato_il  = db.Column(db.DateTime, default=datetime.utcnow)
    abbinato_da  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ddt          = db.relationship('DDT',      backref='abbinamento_ddt_fattura', lazy=True)
    fattura      = db.relationship('Fattura',  backref='abbinamento_ddt',         lazy=True)
    commessa     = db.relationship('Commessa', backref='abbinamenti_ddt',          lazy=True)
    fornitore    = db.relationship('Fornitore', backref='abbinamenti',             lazy=True)


class AbbinatoOrdineSubappalto(db.Model):
    """Abbinamento Ordine ↔ Fattura passiva subappalto, per commessa + descrizione."""
    __tablename__ = 'abbinati_ordine_subappalto'
    id            = db.Column(db.Integer, primary_key=True)
    ordine_id     = db.Column(db.Integer, db.ForeignKey('ordini.id'),    nullable=False)
    fattura_id    = db.Column(db.Integer, db.ForeignKey('fatture.id'),   nullable=False)
    commessa_id   = db.Column(db.Integer, db.ForeignKey('commesse.id'),  nullable=True)
    stato         = db.Column(db.String(20), default='ok')    # 'ok' | 'allarme'
    delta_importo = db.Column(db.Float,    default=0.0)       # fattura.importo - ordine.totale
    score_desc    = db.Column(db.Float,    default=0.0)
    archivio_zip  = db.Column(db.String(500), nullable=True)
    abbinato_il   = db.Column(db.DateTime, default=datetime.utcnow)
    abbinato_da   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ordine   = db.relationship('Ordine',   backref='abbinamento_subappalto', lazy=True)
    fattura  = db.relationship('Fattura',  backref='abbinamento_ordine',     lazy=True)
    commessa = db.relationship('Commessa', backref='abbinamenti_subappalto', lazy=True)


# ── Auth helpers ──────────────────────────────────────────────────────────────

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def ruolo_richiesto(sezione):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('login'))
            if not current_user.ha_accesso(sezione):
                return render_template('403.html'), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def genera_otp():
    return str(random.randint(100000, 999999))


def _sim_descrizione(a: str, b: str) -> float:
    """Percentuale di parole significative in comune tra a e b (0.0-1.0)."""
    if not a or not b:
        return 0.0
    stop = {'di','il','la','le','lo','gli','i','e','in','su','per','a','da',
             'con','non','del','della','dei','degli','delle','un','una','uno',
             'che','si','al','ai','alle','agli','è','ha','nei','nel','nella',
             'nelle','tra','fra','delle','degli'}
    wa = {w.lower() for w in re.findall(r'\w+', a) if len(w) > 3 and w.lower() not in stop}
    wb = {w.lower() for w in re.findall(r'\w+', b) if len(w) > 3 and w.lower() not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _norm_piva(p):
    """Normalizza una P.IVA rimuovendo prefisso IT e zeri iniziali."""
    return re.sub(r'^[Ii][Tt]', '', (p or '').strip()).lstrip('0').upper()


def _fornitore_da_ddt(ddt):
    """Risolve il Fornitore da un DDT: prima per P.IVA diretta,
    poi per corrispondenza nome nel DDT, poi per P.IVA su fatture già caricate."""
    tutti_forn = Fornitore.query.all()

    # 1. P.IVA diretta sul DDT
    if ddt.partita_iva:
        pn = _norm_piva(ddt.partita_iva)
        for fo in tutti_forn:
            if fo.partita_iva and _norm_piva(fo.partita_iva) == pn:
                return fo, pn

    # 2. Nome fornitore DDT → match anagrafica (fuzzy)
    nome_ddt = (ddt.fornitore or '').strip().lower()
    if nome_ddt:
        # match esatto
        for fo in tutti_forn:
            if fo.nome and fo.nome.strip().lower() == nome_ddt:
                return fo, _norm_piva(fo.partita_iva)
        # match parziale (il nome del DDT contiene quello in anagrafica o viceversa)
        for fo in tutti_forn:
            if fo.nome:
                fn = fo.nome.strip().lower()
                if fn in nome_ddt or nome_ddt in fn or _sim_descrizione(nome_ddt, fn) > 0.5:
                    return fo, _norm_piva(fo.partita_iva)

    # 3. Cerca P.IVA nelle fatture già associate a questa commessa
    if ddt.commessa_id:
        ft_comm = Fattura.query.filter(Fattura.commessa_id == ddt.commessa_id,
                                       Fattura.tipo == 'passiva').all()
        for ft in ft_comm:
            if ft.partita_iva_fornitore:
                pn = _norm_piva(ft.partita_iva_fornitore)
                for fo in tutti_forn:
                    if fo.partita_iva and _norm_piva(fo.partita_iva) == pn:
                        return fo, pn

    return None, _norm_piva(ddt.partita_iva) if ddt.partita_iva else None


def _abbina_ddt_fattura(ddt, user_id=None):
    """Abbina un DDT alla fattura passiva materiali per commessa + fornitore + descrizione.
    NON richiede P.IVA diretta sul DDT: la risolve dall'anagrafica fornitori.
    Ritorna (record|None, messaggio)."""
    if not ddt.commessa_id:
        return None, 'DDT senza commessa assegnata: matching impossibile.'

    # ── Risolvi fornitore dall'anagrafica ──
    fornitore, piva_norm = _fornitore_da_ddt(ddt)

    # ── Candidati: fatture passive per questa commessa (tutti i sottotipi) ──
    candidati = Fattura.query.filter(
        Fattura.tipo == 'passiva',
        Fattura.commessa_id == ddt.commessa_id,
    ).all()

    if not candidati:
        # CASO COMUNE: fattura caricata senza commessa_id (admin non ha inserito CLI/COM)
        # → cerca per P.IVA fornitore (qualsiasi commessa, anche NULL)
        fat_tutte_passive = Fattura.query.filter(Fattura.tipo == 'passiva').all()
        if piva_norm:
            candidati = [f for f in fat_tutte_passive
                         if f.partita_iva_fornitore
                         and _norm_piva(f.partita_iva_fornitore) == piva_norm
                         and f.commessa_id in (None, ddt.commessa_id)]
        if not candidati and fornitore:
            # Fallback per nome fornitore
            nome_fo = (fornitore.nome or '').strip().lower()
            candidati = [f for f in fat_tutte_passive
                         if f.cliente_fornitore
                         and _sim_descrizione(f.cliente_fornitore.lower(), nome_fo) > 0.35
                         and f.commessa_id in (None, ddt.commessa_id)]
        if not candidati:
            return None, 'Nessuna fattura passiva trovata per questa commessa o P.IVA fornitore.'

    # ── Priorità 1: stessa commessa + stesso fornitore (P.IVA) ──
    if piva_norm:
        per_piva = [f for f in candidati
                    if f.partita_iva_fornitore and _norm_piva(f.partita_iva_fornitore) == piva_norm]
        if per_piva:
            candidati = per_piva

    # ── Priorità 2: anche match per nome fornitore ──
    elif fornitore:
        nome_fo = (fornitore.nome or '').strip().lower()
        per_nome = [f for f in candidati
                    if f.cliente_fornitore and
                    _sim_descrizione(f.cliente_fornitore.lower(), nome_fo) > 0.4]
        if per_nome:
            candidati = per_nome

    # ── Rank per similarità descrizione DDT ↔ note/descrizione fattura ──
    desc_ddt = ' '.join(filter(None, [ddt.descrizione, ddt.fornitore]))

    def _score_fat(ft):
        testo_ft = ' '.join(filter(None, [ft.note, ft.cliente_fornitore, ft.numero]))
        return _sim_descrizione(desc_ddt, testo_ft) if (desc_ddt and testo_ft) else 0.0

    candidati_scored = sorted(candidati, key=_score_fat, reverse=True)
    best  = candidati_scored[0]
    score = _score_fat(best)

    exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=best.id).first()
    if exist:
        return exist, 'Abbinamento già presente.'

    # Auto-assegna commessa alla fattura se non ce l'ha ancora
    if best.commessa_id is None and ddt.commessa_id:
        best.commessa_id = ddt.commessa_id

    archivio_zip = None
    try:
        import zipfile as _zf
        commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
        cod_comm = (commessa.codice if commessa else 'SENZA_COMMESSA').replace(' ', '_').replace('/', '-')
        dir_zip  = os.path.join(UPLOAD_ROOT, 'archivi', cod_comm)
        os.makedirs(dir_zip, exist_ok=True)
        # anche cartella generale
        dir_gen  = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti')
        os.makedirs(dir_gen, exist_ok=True)
        nome_zip = (f'DDT_{ddt.numero or ddt.id}_FT_{best.numero or best.id}'
                    f'_{cod_comm}.zip').replace(' ', '_').replace('/', '-')
        zip_path = os.path.join(dir_zip, nome_zip)
        with _zf.ZipFile(zip_path, 'w', _zf.ZIP_DEFLATED) as z:
            for _src, prefix in [(ddt.file_path, 'BOLLA'), (best.file_path, 'FATTURA')]:
                if _src:
                    fp = os.path.join(UPLOAD_ROOT, _src)
                    if os.path.exists(fp):
                        z.write(fp, f'{prefix}_{os.path.basename(fp)}')
        # Copia anche nella cartella generale
        import shutil as _shutil
        _shutil.copy2(zip_path, os.path.join(dir_gen, nome_zip))
        archivio_zip = os.path.join('archivi', cod_comm, nome_zip)
    except Exception:
        pass

    record = AbbinatoDdtFattura(
        ddt_id=ddt.id, fattura_id=best.id,
        commessa_id=ddt.commessa_id,
        fornitore_id=fornitore.id if fornitore else None,
        score_desc=round(score, 3),
        archivio_zip=archivio_zip, abbinato_da=user_id,
    )
    db.session.add(record)
    ddt.fattura_id = best.id
    ddt.stato = 'abbinato'
    return record, f'DDT abbinato a Fattura {best.numero or best.id} (score {score:.0%}).'


def _abbina_ordine_subappalto(fattura, commessa, user_id=None, solo_check=False):
    """Abbina fattura passiva subappalto all'ordine della stessa commessa.
    solo_check=True → non crea record, ritorna solo (stato, delta, ordine|None).
    Ritorna (stato, record|None, messaggio, ordine|None).
    stato: 'ok' | 'allarme' | 'nessun_ordine'"""
    if not commessa:
        return 'nessun_ordine', None, 'Nessuna commessa associata.', None

    ordini = Ordine.query.filter_by(commessa_id=commessa.id).all()
    if not ordini:
        return 'nessun_ordine', None, f'Nessun ordine per {commessa.codice}.', None

    desc_ft = (fattura.note or '') + ' ' + (fattura.cliente_fornitore or '') + ' ' + (fattura.numero or '')

    def _desc_ord(o):
        return (' '.join(r.descrizione or '' for r in o.righe) + ' '
                + (o.note or '') + ' ' + (o.dati_cantiere or '') + ' ' + (o.azienda or ''))

    scored = sorted(ordini, key=lambda o: (-_sim_descrizione(desc_ft, _desc_ord(o)), -(o.totale or 0)))
    best = scored[0]

    delta = round((fattura.importo or 0) - (best.totale or 0), 2)
    stato = 'allarme' if delta > 0.01 else 'ok'

    if solo_check:
        return stato, None, '', best

    exist = AbbinatoOrdineSubappalto.query.filter_by(ordine_id=best.id, fattura_id=fattura.id).first()
    if exist:
        return exist.stato, exist, 'Abbinamento già presente.', best

    archivio_zip = None
    try:
        import zipfile as _zf
        dir_zip = os.path.join(UPLOAD_ROOT, 'archivi', 'ordini_subappalti')
        os.makedirs(dir_zip, exist_ok=True)
        nome_zip = (f'ORD_{best.identificativo or best.id}'
                    f'_FT_{fattura.numero or fattura.id}_{commessa.codice}.zip'
                    ).replace(' ', '_').replace('/', '-')
        with _zf.ZipFile(os.path.join(dir_zip, nome_zip), 'w', _zf.ZIP_DEFLATED) as z:
            pdf_ord = os.path.join(UPLOAD_ROOT, 'ordini', f'ordine_{best.id}.pdf')
            if os.path.exists(pdf_ord):
                z.write(pdf_ord, f'ORDINE_{best.identificativo or best.id}.pdf')
            if fattura.file_path:
                fp = os.path.join(UPLOAD_ROOT, fattura.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'FATTURA_SUBAPPALTO_{fattura.numero or fattura.id}_{os.path.basename(fp)}')
        archivio_zip = os.path.join('archivi', 'ordini_subappalti', nome_zip)
    except Exception:
        pass

    record = AbbinatoOrdineSubappalto(
        ordine_id=best.id, fattura_id=fattura.id,
        commessa_id=commessa.id, stato=stato,
        delta_importo=delta,
        score_desc=round(_sim_descrizione(desc_ft, _desc_ord(best)), 3),
        archivio_zip=archivio_zip, abbinato_da=user_id,
    )
    db.session.add(record)
    extra = (f' IMPORTO FATTURA (€ {fattura.importo:,.2f}) > ORDINE (€ {best.totale:,.2f}) — ALLARME' if stato == 'allarme' else '')
    return stato, record, f'Abbinato a ordine {best.identificativo or best.id} (score {_sim_descrizione(desc_ft,_desc_ord(best)):.0%}).{extra}', best


# ── PDF auto-read ──────────────────────────────────────────────────────────────

def _parse_importo(raw: str):
    """Converte una stringa numerica italiana (es. '1.234,56' o '1234.56') in float."""
    raw = raw.strip().replace(' ', '').replace('\u00a0', '')
    raw = raw.lstrip('€').lstrip('EUR').strip()
    if not raw:
        return None
    # Formato italiano: 1.234,56 → ha virgola come decimale
    if ',' in raw and '.' in raw:
        # 1.234,56 → rimuovi punto migliaia, sostituisci virgola
        raw = raw.replace('.', '').replace(',', '.')
    elif ',' in raw:
        # 1234,56 senza punto → virgola è decimale
        raw = raw.replace(',', '.')
    # altrimenti già formato internazionale 1234.56
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_data_it(s: str):
    """Parsa una data italiana in vari formati, restituisce datetime o None."""
    s = s.strip()
    s_norm = re.sub(r'[.\-]', '/', s)
    parts = s_norm.split('/')
    if len(parts) != 3:
        return None
    g, me, a = parts[0].zfill(2), parts[1].zfill(2), parts[2]
    if len(a) == 2:
        a = '20' + a
    if len(a) != 4:
        return None
    try:
        return datetime.strptime(f'{g}/{me}/{a}', '%d/%m/%Y')
    except ValueError:
        return None


def estrai_dati_fattura(testo: str) -> dict:
    """Estrae i campi di una fattura italiana dal testo grezzo del PDF.
    Supporta formati gg/mm/aaaa, gg.mm.aaaa, gg-mm-aaaa, numeri in formato italiano."""
    result = {}
    if not testo or not testo.strip():
        return result

    linee = [l.strip() for l in testo.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    linee_nn = [l for l in linee if l]   # non vuote

    # ── Numero fattura ──────────────────────────────────────────────────────
    for pat in [
        r'(?:fattura(?:\s+(?:n\.?|nr\.?|num\.?|elettronica))?|fatt\.?|ft\.?)\s*[:\s#n°]*([A-Z0-9][A-Z0-9/\-]{0,25})',
        r'\bN[r\.]?\.?\s*[:\s]*(\d{1,6}[A-Z0-9/\-]{0,10})\b',
        r'documento\s*(?:n\.?|nr\.?)?\s*[:\s]*([A-Z0-9/\-]+)',
    ]:
        m = re.search(pat, testo, re.IGNORECASE)
        if m:
            v = m.group(1).strip().rstrip('.-').strip()
            if v and len(v) <= 20:
                result['numero'] = v
                break

    # ── Tutti i valori numerici della fattura, ordinati dal più alto ────────
    # Banca  → il valore PIÙ ALTO trovato nel testo
    # Tecnico → il SECONDO valore più alto (es. imponibile senza IVA)
    # Estrae TUTTI i numeri con aspetto monetario (>=1, con decimali italiani)
    _tutti_grezzi = re.findall(r'\b(\d[\d.]*,\d{1,2})\b|(?:€|EUR)\s*(\d[\d.,]+)', testo)
    _candidati_tutti = []
    for _g in _tutti_grezzi:
        _raw = _g[0] or _g[1]
        _v = _parse_importo(_raw)
        if _v is not None and _v >= 1:
            _candidati_tutti.append(_v)
    # Deduplicati e ordinati dal più alto
    all_candidati = sorted(set(_candidati_tutti), reverse=True)
    if all_candidati:
        result['importo']          = all_candidati[0]   # più alto  → banca
        result['importo_candidati'] = all_candidati
        if len(all_candidati) >= 2:
            result['importo_netto'] = all_candidati[1]  # secondo più alto → tecnico

    # ── Date ────────────────────────────────────────────────────────────────
    DATE_RE = r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b'

    # Scadenza esplicita
    m_sca = re.search(
        r'(?:scadenz[ae]|pagamento\s+(?:a\s+)?entro|entro\s+il|data\s+pagamento|pay\s+by)'
        r'[:\s]*(' + DATE_RE[3:-3] + r')',
        testo, re.IGNORECASE)
    if m_sca:
        d = _parse_data_it(m_sca.group(1))
        if d:
            result['data_scadenza'] = d.strftime('%Y-%m-%d')

    # Emissione esplicita
    m_ems = re.search(
        r'(?:data\s+(?:fattura|emissione|documento|del|emiss\.?)|emessa?\s+(?:il|in\s+data)|del\s+giorno)'
        r'[:\s]*(' + DATE_RE[3:-3] + r')',
        testo, re.IGNORECASE)
    if m_ems:
        d = _parse_data_it(m_ems.group(1))
        if d:
            result['data_emissione'] = d.strftime('%Y-%m-%d')

    # Fallback: tutte le date nel testo
    all_raw_dates = re.findall(DATE_RE, testo)
    parsed_dates = []
    for ds in all_raw_dates:
        d = _parse_data_it(ds)
        if d and 2000 <= d.year <= 2099:   # date ragionevoli
            parsed_dates.append(d)
    if parsed_dates:
        if 'data_emissione' not in result:
            result['data_emissione'] = parsed_dates[0].strftime('%Y-%m-%d')
        if 'data_scadenza' not in result and len(parsed_dates) > 1:
            result['data_scadenza'] = parsed_dates[-1].strftime('%Y-%m-%d')

    # Stima scadenza da "pagamento a NN giorni" se non trovata
    if 'data_scadenza' not in result and 'data_emissione' in result:
        m_gg = re.search(
            r'(?:pagamento|scadenza)\s+(?:a|di|in|entro)\s+(\d{2,3})\s*(?:gg\.?|giorni)',
            testo, re.IGNORECASE)
        if m_gg:
            ems = datetime.strptime(result['data_emissione'], '%Y-%m-%d')
            sca = ems + timedelta(days=int(m_gg.group(1)))
            result['data_scadenza'] = sca.strftime('%Y-%m-%d')

    # ── Fornitore / emittente ───────────────────────────────────────────────
    # Strategia 1: keyword esplicita nel testo
    m_forn = re.search(
        r'(?:cedente|prestatore|mittente|emittente|fornitore|venditore|ditta\s+fornitrice)'
        r'[:\s]+([^\n]{3,100})',
        testo, re.IGNORECASE)
    if m_forn:
        result['cliente_fornitore'] = m_forn.group(1).strip()[:100]

    # Strategia 2: prima delle parole "Spett" / "Egr." / "All'attenzione"
    if 'cliente_fornitore' not in result:
        m_spett = re.search(r'\b(?:spett(?:\.?|abile)|egregio|all.attenzione)\b', testo, re.IGNORECASE)
        pos_spett = m_spett.start() if m_spett else len(testo)
        # Prende le prime 8 linee non vuote PRIMA di "Spett." come candidato fornitore
        testo_prima = testo[:pos_spett]
        linee_prima = [l.strip() for l in testo_prima.split('\n') if l.strip()][:8]
        for linea in linee_prima:
            # Linea significativa: min 4 char, non solo numeri/simboli, non una data o P.IVA
            if (len(linea) >= 4
                    and not re.fullmatch(r'[\d/.\-\s€,:;]+', linea)
                    and not re.search(r'partita\s*iva|p\.?\s*iva|c\.?\s*f\.?|pag(?:ina)?\.?\s*\d', linea, re.IGNORECASE)
                    and not re.match(r'\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}', linea)):
                result['cliente_fornitore'] = linea[:100]
                break

    # Strategia 3: se ancora nulla, prendi la prima riga non vuota del testo
    if 'cliente_fornitore' not in result and linee_nn:
        candid = linee_nn[0]
        if len(candid) >= 4 and not re.fullmatch(r'[\d/.\-\s€,:;]+', candid):
            result['cliente_fornitore'] = candid[:100]

    # ── Partita IVA fornitore ───────────────────────────────────────────────
    # Cerca vicino a keyword emittente (entro 300 char dopo)
    m_piva = re.search(
        r'(?:cedente|prestatore|mittente|emittente|fornitore|venditore|ditta)[^\n]{0,300}'
        r'(?:p\.?\s*i(?:va)?\.?|partita\s*iva)\s*[:\s]*([IT]?\d{11})',
        testo, re.IGNORECASE | re.DOTALL)
    if m_piva:
        result['partita_iva'] = m_piva.group(1).strip()
    else:
        # Prima P.IVA nel documento (di norma è quella del fornitore)
        tutte = re.findall(
            r'(?:p\.?\s*i(?:va)?\.?|partita\s*iva)\s*[:\s]*([IT]?\d{11})',
            testo, re.IGNORECASE)
        if tutte:
            result['partita_iva'] = tutte[0].strip()

    # ── Sottotipo: subappalto vs materiali ─────────────────────────────────
    tl = testo.lower()
    sub_kw = ['subappalto', 'subappaltatore', 'nolo', 'noleggio', 'manodopera',
              'prestazione', 'servizi edili', 'lavori edili', 'lavorazioni edili',
              'installazione', 'montaggio']
    mat_kw = ['materiali', 'fornitura', 'prodotti', 'merci', 'articoli', 'forniture',
              'materiale da costruzione', 'componenti', 'ferro', 'calcestruzzo']
    sc = sum(1 for k in sub_kw if k in tl)
    mc = sum(1 for k in mat_kw if k in tl)
    result['sottotipo'] = 'subappalto' if sc > mc else 'materiali'

    return result



def _estrai_sezioni_commessa(testo: str) -> list:
    """Divide una fattura con più codici commessa in sezioni.
    Cerca pattern C{cl}-{cm} nel testo, poi per ogni sezione calcola il totale
    sommando qta × prezzo per riga.
    Restituisce lista di dict se ≥2 sezioni distinte, altrimenti lista vuota.
    """
    if not testo:
        return []

    # Trova tutte le occorrenze di codice commessa con la loro posizione nel testo
    COMM_PATS = [
        r'(?:commessa|cod(?:ice)?\.?\s*comm(?:essa)?\.?|rif(?:erimento)?\.?\s*comm(?:essa)?)'
        r'\s*[:\s#]*C?0*(\d{1,4})[-/]0*(\d{1,4})',
        r'C0*(\d{1,4})[-/]0*(\d{1,4})',
    ]
    hits = []   # (pos, cod_cl, cod_cm)
    for pat in COMM_PATS:
        for m in re.finditer(pat, testo, re.IGNORECASE):
            hits.append((m.start(), m.group(1), m.group(2)))

    if not hits:
        return []

    # Dedup per chiave (cl, cm), mantenendo prima occorrenza
    seen = set()
    unique_hits = []
    for pos, cl, cm in sorted(hits):
        key = (cl.lstrip('0') or '0', cm.lstrip('0') or '0')
        if key not in seen:
            seen.add(key)
            unique_hits.append((pos, cl, cm))

    if len(unique_hits) < 2:
        return []

    def _parse_n(s):
        s = str(s).strip().replace(' ', '').replace(' ', '')
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            v = float(s)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    results = []
    for i, (pos, cl, cm) in enumerate(unique_hits):
        end = unique_hits[i + 1][0] if i + 1 < len(unique_hits) else len(testo)
        sect = testo[pos:end]

        # Lookup commessa in DB
        commessa_obj = None
        try:
            n_cl = int(cl)
            n_cm = int(cm)
            _cli = Cliente.query.filter_by(numero=n_cl).first()
            if _cli:
                commessa_obj = Commessa.query.filter_by(
                    cliente_id=_cli.id, numero=n_cm
                ).first()
        except (ValueError, TypeError):
            pass

        # Estrai righe e calcola totale per questa sezione
        righe = []
        totale = 0.0
        linee = [l.strip() for l in sect.split('\n') if l.strip()]
        for linea in linee:
            # Ignora la riga del codice commessa stessa
            if re.match(r'^C?\d{1,4}[-/]\d{1,4}$', linea, re.IGNORECASE):
                continue
            nums = re.findall(r'\b\d[\d.]*,\d{1,2}\b', linea)
            if not nums:
                continue
            parsed = [v for v in (_parse_n(n) for n in nums) if v is not None]
            if not parsed:
                continue
            desc = re.sub(r'[\d.,]+', '', linea).strip()
            desc = re.sub(r'\s+', ' ', desc)[:200]
            # Logica importo riga
            if len(parsed) == 1:
                imp = parsed[0]
                righe.append({'descrizione': desc, 'quantita': None,
                              'prezzo_unitario': None, 'importo': imp})
                totale += imp
            else:
                ult = parsed[-1]
                qta = parsed[0]
                pu  = parsed[1] if len(parsed) >= 3 else parsed[-1]
                if qta > 0 and pu > 0 and abs(qta * pu - ult) < max(ult * 0.05, 0.05):
                    righe.append({'descrizione': desc, 'quantita': qta,
                                  'prezzo_unitario': pu, 'importo': round(qta * pu, 2)})
                    totale += round(qta * pu, 2)
                elif len(parsed) == 2:
                    # 2 numeri senza importo esplicito: qta × pu
                    imp = round(parsed[0] * parsed[1], 2)
                    righe.append({'descrizione': desc, 'quantita': parsed[0],
                                  'prezzo_unitario': parsed[1], 'importo': imp})
                    totale += imp
                else:
                    righe.append({'descrizione': desc, 'quantita': None,
                                  'prezzo_unitario': None, 'importo': ult})
                    totale += ult

        codice_raw = f"C{cl}-{cm}"
        results.append({
            'codice_raw':    codice_raw,
            'commessa_id':   commessa_obj.id if commessa_obj else None,
            'commessa_nome': (f"{commessa_obj.codice} — {commessa_obj.nome}"
                              if commessa_obj else codice_raw),
            'cliente_nome':  (commessa_obj.cliente.nome
                              if commessa_obj and commessa_obj.cliente else ''),
            'righe':  righe,
            'totale': round(totale, 2),
        })

    return results


def estrai_righe_fattura(pdf_bytes):
    """Estrae le righe della tabella fattura da un PDF usando pdfplumber.extract_tables().
    Restituisce lista di dict: {descrizione, quantita, prezzo_unitario, importo}."""
    righe = []
    if not PDF_SUPPORT:
        return righe

    HEADER_DESC = ['descrizione', 'lavori', 'articoli', 'prestaz', 'oggetto', 'causale', 'servizi']
    HEADER_QTA  = ['quant', 'qta', 'q.t', 'q/t', 'pezzi', 'num']
    HEADER_PU   = ['unit', 'p.u', 'p/u', 'prezzo u', 'costo u', 'listino','prezzo']
    HEADER_IMP  = ['import', 'totale', 'prezzo', 'tot.', 'amount', 'valore']

    def _num(s):
        if not s:
            return None
        s = str(s).strip().replace(' ', '').replace('.', '').replace(',', '.')
        try:
            return float(s)
        except ValueError:
            return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header_row = [str(c or '').lower().strip() for c in table[0]]

                    def _find(kws):
                        return next((i for i, h in enumerate(header_row)
                                     if any(k in h for k in kws)), None)

                    idx_d = _find(HEADER_DESC)
                    idx_q = _find(HEADER_QTA)
                    idx_u = _find(HEADER_PU)
                    idx_i = _find(HEADER_IMP)

                    # Almeno descrizione o importo devono essere rilevabili
                    if idx_d is None and idx_i is None:
                        continue

                    for row in table[1:]:
                        if not row or all(not c for c in row):
                            continue

                        def cel(i):
                            if i is None or i >= len(row):
                                return ''
                            return str(row[i] or '').strip()

                        desc = cel(idx_d)
                        if not desc or len(desc) < 2:
                            continue
                        # Salta righe che sembrano header ripetuti
                        if any(k in desc.lower() for k in HEADER_DESC):
                            continue

                        qta = _num(cel(idx_q))
                        pu  = _num(cel(idx_u))
                        imp = _num(cel(idx_i))

                        # Calcola importo se mancante ma qta e pu presenti
                        if imp is None and qta is not None and pu is not None:
                            imp = round(qta * pu, 2)

                        righe.append({
                            'descrizione':     desc[:300],
                            'quantita':        qta,
                            'prezzo_unitario': pu,
                            'importo':         imp,
                        })
    except Exception:
        pass

    return righe


def get_soglia_corrente():
    """Restituisce la soglia di approvazione dal nodo con soglia minima nell'organigramma,
    oppure il valore predefinito SOGLIA_APPROVAZIONE."""
    try:
        nodo = NodoOrganico.query.filter(
            NodoOrganico.soglia_approvazione.isnot(None)
        ).order_by(NodoOrganico.soglia_approvazione.asc()).first()
        return nodo.soglia_approvazione if nodo else SOGLIA_APPROVAZIONE
    except Exception:
        return SOGLIA_APPROVAZIONE


def ctx():
    return dict(sezioni=current_user.sezioni_accessibili(), sezioni_label=SEZIONI_LABEL)


def _scadenza_bucket(data_scad):
    if not data_scad:
        return None
    oggi = date.today()
    delta = (data_scad - oggi).days
    if delta <= 0:
        return 'oggi'
    elif delta <= 5:
        return '5gg'
    elif delta <= 10:
        return '10gg'
    elif delta <= 20:
        return '20gg'
    elif delta <= 30:
        return '30gg'
    elif delta <= 60:
        return '60gg'
    elif delta <= 90:
        return '90gg'
    elif delta <= 120:
        return '120gg'
    else:
        return '150gg'


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/healthz')
def healthz():
    return 'ok', 200


@app.route('/')
def index():
    if current_user.is_authenticated:
        sezioni = current_user.sezioni_accessibili()
        if sezioni:
            return redirect(url_for(sezioni[0]))
    return redirect(url_for('login'))


@app.route('/registrazione', methods=['GET', 'POST'])
def registrazione():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conferma = request.form.get('conferma_password', '')
        telefono = request.form.get('telefono', '').strip()
        ruolo = request.form.get('ruolo', '')

        if not all([email, password, conferma, telefono, ruolo]):
            flash('Tutti i campi sono obbligatori.', 'danger')
            return render_template('registrazione.html', ruoli=RUOLI)
        if password != conferma:
            flash('Le password non coincidono.', 'danger')
            return render_template('registrazione.html', ruoli=RUOLI)
        if len(password) < 8:
            flash('La password deve essere di almeno 8 caratteri.', 'danger')
            return render_template('registrazione.html', ruoli=RUOLI)
        if ruolo not in RUOLI:
            flash('Ruolo non valido.', 'danger')
            return render_template('registrazione.html', ruoli=RUOLI)
        if User.query.filter_by(email=email).first():
            flash('Email già registrata. Effettua il login.', 'warning')
            return redirect(url_for('login'))

        nome = request.form.get('nome', '').strip()
        user = User(nome=nome or None, email=email, telefono=telefono, ruolo=ruolo)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registrazione completata! Ora effettua il login.', 'success')
        return redirect(url_for('login'))

    return render_template('registrazione.html', ruoli=RUOLI)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash('Email o password non corretti.', 'danger')
            return render_template('login.html')
        if not user.attivo:
            flash('Il tuo account è stato disabilitato. Contatta la Direzione.', 'danger')
            return render_template('login.html')
        otp = genera_otp()
        session['otp_code'] = otp
        session['otp_user_id'] = user.id
        session['otp_telefono'] = user.telefono
        return redirect(url_for('verifica_otp'))
    return render_template('login.html')


@app.route('/verifica-otp', methods=['GET', 'POST'])
def verifica_otp():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if 'otp_user_id' not in session:
        flash('Sessione scaduta. Rieffettua il login.', 'warning')
        return redirect(url_for('login'))
    otp_generato = session.get('otp_code')
    telefono = session.get('otp_telefono', '')
    tel_mascherato = telefono[:-4].replace(telefono[:-4], '*' * len(telefono[:-4])) + telefono[-4:] if len(telefono) > 4 else telefono
    if request.method == 'POST':
        otp_inserito = request.form.get('otp', '').strip()
        if otp_inserito == otp_generato:
            user = db.session.get(User, session['otp_user_id'])
            if user:
                login_user(user)
                session.pop('otp_code', None)
                session.pop('otp_user_id', None)
                session.pop('otp_telefono', None)
                flash(f'Benvenuto, {user.email}!', 'success')
                return redirect(url_for('index'))
        else:
            flash('Codice OTP non corretto. Riprova.', 'danger')
    return render_template('verifica_otp.html', tel_mascherato=tel_mascherato, otp_demo=otp_generato)


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logout effettuato con successo.', 'info')
    return redirect(url_for('login'))


# ── Tecnico ───────────────────────────────────────────────────────────────────

@app.route('/tecnico')
@login_required
@ruolo_richiesto('tecnico')
def tecnico():
    q = Cliente.query
    if current_user.ruolo == 'Tecnico':
        q = q.filter(Cliente.owner == current_user.nome)
    clienti = q.order_by(Cliente.nome).all()
    responsabili = NodoOrganico.query.filter(
        NodoOrganico.posizione.ilike('%direttore tecnico%')
    ).order_by(NodoOrganico.nome).all()
    n_fatture_pending = Fattura.query.filter_by(tipo='passiva', stato_approvazione='pending').count()
    if current_user.ruolo == 'Direzione':
        n_bolle_da_approvare = DDT.query.filter_by(stato='in_attesa_tecnico').count()
    else:
        n_bolle_da_approvare = DDT.query.filter_by(
            stato='in_attesa_tecnico', tecnico_id=current_user.id
        ).count()
    return render_template('tecnico.html', clienti=clienti, responsabili=responsabili,
                           n_fatture_pending=n_fatture_pending,
                           n_bolle_da_approvare=n_bolle_da_approvare, **ctx())


def _next_commessa_numero(cliente_id):
    """Numero commessa progressivo per cliente (1, 2, 3… indipendente per ogni cliente)."""
    max_num = db.session.query(db.func.max(Commessa.numero)).filter_by(cliente_id=cliente_id).scalar()
    return (max_num or 0) + 1


def _next_cliente_numero():
    """Numero progressivo cliente globale (C001, C002…)."""
    max_num = db.session.query(db.func.max(Cliente.numero)).scalar()
    return (max_num or 0) + 1


@app.route('/api/next-commessa-numero/<int:cliente_id>')
@login_required
def api_next_commessa_numero(cliente_id):
    return jsonify({'numero': _next_commessa_numero(cliente_id)})


@app.route('/tecnico/nuovo-cliente', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def nuovo_cliente():
    nome  = request.form.get('nome', '').strip()
    piva  = request.form.get('partita_iva', '').strip() or None
    cig   = request.form.get('cig', '').strip() or None
    cup   = request.form.get('cup', '').strip() or None
    owner = request.form.get('owner', '').strip() or None
    c_nome  = request.form.get('commessa_nome', '').strip() or 'Commessa principale'
    c_luogo = request.form.get('commessa_luogo', '').strip() or None
    c_via   = request.form.get('commessa_via',   '').strip() or None
    c_num   = request.form.get('commessa_numero', '').strip()
    if not nome:
        flash('Il nome del cliente è obbligatorio.', 'danger')
        return redirect(url_for('tecnico'))
    if Cliente.query.filter_by(nome=nome).first():
        flash(f'Esiste già un cliente con il nome "{nome}".', 'warning')
        return redirect(url_for('tecnico'))
    num_cliente = _next_cliente_numero()
    cliente = Cliente(nome=nome, numero=num_cliente, partita_iva=piva,
                      cig=cig, cup=cup, owner=owner)
    db.session.add(cliente)
    db.session.flush()
    try:
        num_commessa = int(c_num) if c_num else _next_commessa_numero(cliente.id)
    except ValueError:
        num_commessa = _next_commessa_numero(cliente.id)
    db.session.add(Commessa(
        cliente_id=cliente.id, numero=num_commessa,
        nome=c_nome, luogo=c_luogo, via=c_via, owner=owner
    ))
    db.session.commit()
    tipo = 'Pubblico (CIG/CUP)' if (cig or cup) else 'Privato'
    flash(f'Cliente C{num_cliente:03d} "{nome}" ({tipo}) e commessa N°{num_commessa} "{c_nome}" creati.', 'success')
    return redirect(url_for('lista_commesse', cliente_id=cliente.id))


@app.route('/tecnico/elimina-cliente/<int:cliente_id>', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_cliente(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    db.session.delete(cliente)
    db.session.commit()
    flash(f'Cliente "{cliente.nome}" eliminato.', 'info')
    return redirect(url_for('tecnico'))


def _check_cliente_access(cliente):
    """Restituisce True se l'utente corrente può accedere al cliente dato."""
    if current_user.ruolo == 'Tecnico':
        return cliente and cliente.owner == current_user.nome
    return bool(cliente)


@app.route('/tecnico/cliente/<int:cliente_id>')
@login_required
@ruolo_richiesto('tecnico')
def lista_commesse(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente or not _check_cliente_access(cliente):
        flash('Cliente non trovato o accesso non autorizzato.', 'danger')
        return redirect(url_for('tecnico'))
    responsabili = NodoOrganico.query.filter(
        NodoOrganico.posizione.ilike('%direttore tecnico%')
    ).order_by(NodoOrganico.nome).all()
    return render_template('cliente_commesse.html', cliente=cliente,
                           responsabili=responsabili, **ctx())


@app.route('/tecnico/cliente/<int:cliente_id>/nuova-commessa', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def nuova_commessa(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    nome  = request.form.get('nome', '').strip() or 'Nuova Commessa'
    luogo = request.form.get('luogo', '').strip() or None
    via   = request.form.get('via', '').strip() or None
    owner = request.form.get('owner', '').strip() or None
    num = _next_commessa_numero(cliente_id)
    db.session.add(Commessa(cliente_id=cliente_id, numero=num,
                            nome=nome, luogo=luogo, via=via, owner=owner))
    db.session.commit()
    flash(f'Commessa n°{num} "{nome}" aggiunta.', 'success')
    return redirect(url_for('lista_commesse', cliente_id=cliente_id))


@app.route('/tecnico/commessa/<int:commessa_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_commessa(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico'))
    cliente_id = commessa.cliente_id
    nome = commessa.nome
    db.session.delete(commessa)
    db.session.commit()
    flash(f'Commessa "{nome}" eliminata.', 'info')
    return redirect(url_for('lista_commesse', cliente_id=cliente_id))




# ════════════════════════════════════════════════════════════════════
#  KPI – Dashboard indicatori per commessa
# ════════════════════════════════════════════════════════════════════

@app.route('/kpi')
@login_required
@ruolo_richiesto('kpi')
def kpi_dashboard():
    """Calcola i KPI per ogni commessa attingendo dai costi del tecnico."""
    commesse = Commessa.query.order_by(Commessa.cliente_id, Commessa.numero).all()
    righe = []
    for commessa in commesse:
        c_mat  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='materiali').all())
        c_sub  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='subappalti').all())
        c_per  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='personale').all())
        passivo = c_mat + c_sub + c_per

        fatture_att = Fattura.query.filter_by(tipo='attiva', commessa_id=commessa.id).all()
        ordini_comm = Ordine.query.filter_by(commessa_id=commessa.id).all()
        attivo = sum(f.importo for f in fatture_att) + sum(o.totale or 0 for o in ordini_comm)

        # KPI1
        denominatore_k1 = c_sub + c_mat + c_per
        if denominatore_k1 > 0:
            k1 = attivo / denominatore_k1
            if k1 < 1.0:
                k1_label = 'PERDITA'
                k1_color = 'danger'
            elif k1 < 1.15:
                k1_label = 'Scarso guadagno'
                k1_color = 'warning'
            else:
                k1_label = 'OK'
                k1_color = 'success'
        else:
            k1 = None
            k1_label = '–'
            k1_color = 'secondary'

        # KPI2, KPI3, KPI4 (percentuale su passivo totale)
        k2 = round(c_per  / passivo * 100, 1) if passivo > 0 else None
        k3 = round(c_mat  / passivo * 100, 1) if passivo > 0 else None
        k4 = round(c_sub  / passivo * 100, 1) if passivo > 0 else None

        righe.append({
            'commessa':  commessa,
            'codice':    commessa.codice,
            'nome':      commessa.nome,
            'attivo':    attivo,
            'passivo':   passivo,
            'c_mat':     c_mat,
            'c_sub':     c_sub,
            'c_per':     c_per,
            'k1':        round(k1, 2) if k1 is not None else None,
            'k1_label':  k1_label,
            'k1_color':  k1_color,
            'k2':        k2,
            'k3':        k3,
            'k4':        k4,
        })
    # ordina: prima le commesse con dati, poi quelle vuote
    righe.sort(key=lambda r: (r['passivo'] == 0 and r['attivo'] == 0, r['codice']))
    return render_template('kpi.html', righe=righe, **ctx())

@app.route('/tecnico/commessa/<int:commessa_id>')
@login_required
@ruolo_richiesto('tecnico')
def commessa_detail(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa or not _check_cliente_access(commessa.cliente):
        flash('Commessa non trovata o accesso non autorizzato.', 'danger')
        return redirect(url_for('tecnico'))
    cliente = commessa.cliente
    categorie = ['materiali', 'subappalti', 'personale']
    costi = {}
    totali = {}
    for cat in categorie:
        voci = CostoFornitore.query.filter_by(commessa_id=commessa_id, categoria=cat).order_by(CostoFornitore.data.desc()).all()
        costi[cat] = voci
        totali[cat] = sum(v.importo for v in voci)
    tab    = request.args.get('tab',    'materiali')
    subtab = request.args.get('subtab', 'ore')
    sub_totali = {}
    for sub in ['ore', 'pasti', 'km']:
        sub_totali[sub] = sum(v.importo for v in costi['personale'] if v.sottocategoria == sub)
    fatture_passive = Fattura.query.filter_by(
        tipo='passiva', commessa_id=commessa_id
    ).order_by(Fattura.data_scadenza).all()
    fatture_attive = Fattura.query.filter_by(
        tipo='attiva', commessa_id=commessa_id
    ).order_by(Fattura.data_scadenza).all()
    ordini_commessa = Ordine.query.filter_by(
        commessa_id=commessa_id
    ).order_by(Ordine.creato_il.desc()).all()
    oggi_d = date.today()
    # Dati riepilogo per istogrammi
    totale_fatture_passive = sum(f.importo for f in fatture_passive)
    totale_fatture_attive  = sum((f.importo_netto or f.importo) for f in fatture_attive)  # tecnico: senza IVA
    ordini_tot = sum(o.totale or 0 for o in cliente.ordini)
    riepilogo = {
        'attivo_ordini':      round(ordini_tot, 2),
        'attivo_fatture':     round(totale_fatture_attive, 2),
        'attivo':             round(ordini_tot + totale_fatture_attive, 2),
        'passivo_materiali':  round(totali['materiali'], 2),
        'passivo_subappalti': round(totali['subappalti'], 2),
        'passivo_personale':  round(totali['personale'], 2),
        'passivo_fatture':    round(totale_fatture_passive, 2),
    }
    # Bolle in attesa di approvazione per questa commessa
    ddt_in_attesa = DDT.query.filter_by(
        commessa_id=commessa_id, stato='in_attesa_tecnico'
    ).order_by(DDT.creato_il.desc()).all()

    # Righe DDT per questa commessa (da tutte le bolle, anche multi-commessa)
    righe_ddt_commessa = (RigaDDT.query
                          .filter_by(commessa_id=commessa_id)
                          .order_by(RigaDDT.ddt_id, RigaDDT.id)
                          .all())
    # Raggruppa righe per DDT
    from itertools import groupby
    righe_per_ddt = {}
    for riga in righe_ddt_commessa:
        righe_per_ddt.setdefault(riga.ddt_id, []).append(riga)
    # Calcola totale righe DDT per questa commessa
    totale_righe_ddt = sum(r.calc_importo() for r in righe_ddt_commessa)

    return render_template('costi_fornitori.html',
                           commessa=commessa, cliente=cliente,
                           costi=costi, totali=totali,
                           tab=tab, subtab=subtab, sub_totali=sub_totali,
                           fatture_passive=fatture_passive,
                           fatture_attive=fatture_attive,
                           ordini_commessa=ordini_commessa,
                           riepilogo=riepilogo,
                           ddt_in_attesa=ddt_in_attesa,
                           righe_ddt_commessa=righe_ddt_commessa,
                           righe_per_ddt=righe_per_ddt,
                           totale_righe_ddt=totale_righe_ddt,
                           oggi=oggi_d, **ctx())


# ── API JSON: commesse per cliente (usata da amministrazione.html) ─────────────
@app.route('/api/commesse-per-cliente/<int:cliente_id>')
@login_required
def api_commesse_per_cliente(cliente_id):
    commesse = Commessa.query.filter_by(cliente_id=cliente_id).order_by(Commessa.numero).all()
    return jsonify([{'id': c.id, 'numero': c.numero, 'nome': c.nome} for c in commesse])


# Alias backward-compat: vecchio URL /tecnico/cliente/<id>/costi
@app.route('/tecnico/cliente/<int:cliente_id>/costi')
@login_required
@ruolo_richiesto('tecnico')
def costi_fornitori(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if cliente and cliente.commesse:
        return redirect(url_for('commessa_detail', commessa_id=cliente.commesse[0].id))
    return redirect(url_for('lista_commesse', cliente_id=cliente_id))


@app.route('/tecnico/commessa/<int:commessa_id>/aggiungi-costo', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def aggiungi_costo(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico'))
    categoria      = request.form.get('categoria', '')
    sottocategoria = request.form.get('sottocategoria', '') or None
    descrizione    = request.form.get('descrizione', '').strip()
    importo_str    = request.form.get('importo', '0').replace(',', '.')
    quantita_str   = request.form.get('quantita', '').replace(',', '.')
    valore_str     = request.form.get('valore_unitario', '').replace(',', '.')
    data_str       = request.form.get('data', '')

    if not descrizione or not categoria:
        flash('Descrizione e categoria sono obbligatorie.', 'danger')
        return redirect(url_for('commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))

    quantita        = float(quantita_str) if quantita_str else None
    valore_unitario = float(valore_str)   if valore_str   else None

    if quantita is not None and valore_unitario is not None:
        importo = quantita * valore_unitario
    else:
        try:
            importo = float(importo_str)
        except ValueError:
            importo = 0.0

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()

    voce = CostoFornitore(
        commessa_id=commessa_id, cliente_id=commessa.cliente_id,
        categoria=categoria, sottocategoria=sottocategoria,
        descrizione=descrizione, quantita=quantita, valore_unitario=valore_unitario,
        importo=importo, data=data
    )
    db.session.add(voce)
    db.session.commit()
    flash('Voce aggiunta con successo.', 'success')
    return redirect(url_for('commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))


@app.route('/tecnico/costo/<int:costo_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_costo(costo_id):
    voce = db.session.get(CostoFornitore, costo_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('tecnico'))
    commessa_id    = voce.commessa_id
    categoria      = voce.categoria
    sottocategoria = voce.sottocategoria
    db.session.delete(voce)
    db.session.commit()
    flash('Voce eliminata.', 'info')
    if commessa_id:
        return redirect(url_for('commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))
    return redirect(url_for('tecnico'))


@app.route('/tecnico/tutti-ordini')
@login_required
@ruolo_richiesto('tecnico')
def tutti_ordini():
    clienti_acc = [c for c in Cliente.query.order_by(Cliente.nome).all() if _check_cliente_access(c)]
    clienti_ids = [c.id for c in clienti_acc]
    ordini = Ordine.query.filter(Ordine.cliente_id.in_(clienti_ids)).order_by(Ordine.creato_il.desc()).all()
    return render_template('tutti_ordini.html', ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@app.route('/tecnico/cliente/<int:cliente_id>/ordini')
@login_required
@ruolo_richiesto('tecnico')
def ordini_lista(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    ordini = Ordine.query.filter_by(cliente_id=cliente_id).order_by(Ordine.creato_il.desc()).all()
    return render_template('ordini_lista.html', cliente=cliente, ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@app.route('/tecnico/cliente/<int:cliente_id>/ordini/nuovo', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('tecnico')
def ordine_nuovo(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    # commessa opzionale passata come query param o form field
    commessa_id_pre = request.args.get('commessa_id') or request.form.get('commessa_id_pre')
    commessa_pre = None
    if commessa_id_pre:
        try:
            commessa_pre = db.session.get(Commessa, int(commessa_id_pre))
        except (ValueError, TypeError):
            pass
    if request.method == 'POST':
        commessa_id_form = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_form:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_form))
            except (ValueError, TypeError):
                pass
        # Genera identificativo automatico
        identificativo = ''
        if commessa:
            num_cl = cliente.numero or cliente.id
            identificativo = f'C{num_cl:03d}-{commessa.numero}'
        ordine = Ordine(
            cliente_id=cliente_id,
            commessa_id=commessa.id if commessa else None,
            identificativo=identificativo,
            azienda=request.form.get('azienda', '').strip(),
            piva=request.form.get('piva', '').strip(),
            rif_ordine=request.form.get('rif_ordine', '').strip() or identificativo,
            dati_cantiere=request.form.get('dati_cantiere', '').strip(),
            indirizzo=request.form.get('indirizzo', '').strip(),
            rif_cantiere=request.form.get('rif_cantiere', '').strip(),
            note=request.form.get('note', '').strip(),
            cig=request.form.get('cig', '').strip() or None,
            cup=request.form.get('cup', '').strip() or None,
            creato_da=current_user.id
        )
        db.session.add(ordine)
        db.session.flush()
        descrizioni = request.form.getlist('descrizione[]')
        qta_list = request.form.getlist('qta[]')
        um_list = request.form.getlist('um[]')
        prezzi = request.form.getlist('prezzo_unitario[]')
        totale_ordine = 0.0
        for i, desc in enumerate(descrizioni):
            if not desc.strip():
                continue
            try:
                qta = float(qta_list[i].replace(',', '.')) if i < len(qta_list) else 0
                prezzo = float(prezzi[i].replace(',', '.')) if i < len(prezzi) else 0
            except (ValueError, IndexError):
                qta, prezzo = 0, 0
            tot_riga = qta * prezzo
            totale_ordine += tot_riga
            db.session.add(RigaOrdine(ordine_id=ordine.id, numero=i+1, descrizione=desc.strip(),
                                       qta=qta, unita_misura=um_list[i] if i < len(um_list) else '',
                                       prezzo_unitario=prezzo, totale=tot_riga))
        ordine.totale = totale_ordine
        soglia = get_soglia_corrente()
        if totale_ordine > soglia:
            ordine.stato = 'in_attesa'
            flash(f'Ordine creato. Totale € {totale_ordine:,.2f} — supera la soglia di € {soglia:,.0f}: in attesa approvazione Responsabili.', 'warning')
        else:
            ordine.stato = 'approvato'
            flash(f'Ordine approvato automaticamente. Totale: € {totale_ordine:,.2f}', 'success')
        db.session.commit()
        return redirect(url_for('ordini_lista', cliente_id=cliente_id))
    commesse_cliente = Commessa.query.filter_by(cliente_id=cliente_id).order_by(Commessa.numero).all()
    return render_template('ordine_nuovo.html', cliente=cliente,
                           commesse_cliente=commesse_cliente, commessa_pre=commessa_pre,
                           soglia=get_soglia_corrente(), **ctx())


@app.route('/tecnico/ordine/<int:ordine_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    cliente_id = ordine.cliente_id
    cod = f'ORD-{ordine.id:04d}'
    db.session.delete(ordine)
    db.session.commit()
    flash(f'Ordine {cod} eliminato.', 'info')
    return redirect(url_for('ordini_lista', cliente_id=cliente_id))


@app.route('/tecnico/ordine/<int:ordine_id>')
@login_required
@ruolo_richiesto('tecnico')
def ordine_dettaglio(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico'))
    return render_template('ordine_dettaglio.html', ordine=ordine, cliente=ordine.cliente,
                           soglia=get_soglia_corrente(), **ctx())


# ── PDF Ordine ────────────────────────────────────────────────────────────────

@app.route('/tecnico/ordine/<int:ordine_id>/pdf')
@login_required
@ruolo_richiesto('tecnico')
def ordine_pdf(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico'))

    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, HRFlowable)
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    # ── Palette ──────────────────────────────────────────────────────────────
    C_DARK   = colors.HexColor('#1e1b4b')   # indigo scuro
    C_MID    = colors.HexColor('#4338ca')   # indigo medio
    C_LIGHT  = colors.HexColor('#e0e7ff')   # indigo chiaro
    C_ACCENT = colors.HexColor('#6366f1')   # viola accento
    C_GREY   = colors.HexColor('#64748b')   # grigio testo
    C_ROW    = colors.HexColor('#f5f3ff')   # riga alternata
    C_WHITE  = colors.white
    C_BLACK  = colors.HexColor('#0f172a')

    # ── Stili testo ──────────────────────────────────────────────────────────
    def ps(name, fontName='Helvetica-Bold', fontSize=10, textColor=C_BLACK,
           alignment=TA_LEFT, leading=None, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=fontName, fontSize=fontSize,
                              textColor=textColor, alignment=alignment,
                              leading=leading or fontSize * 1.3,
                              spaceBefore=spaceBefore, spaceAfter=spaceAfter)

    sty_brand_big   = ps('BB',  'Helvetica-Bold',  33, C_WHITE, TA_LEFT)
    sty_brand_sub   = ps('BS',  'Helvetica-Bold',  9,  colors.HexColor('#fbbf24'), TA_LEFT)
    sty_label       = ps('LB',  'Helvetica',        7, C_GREY,  TA_LEFT)
    sty_value       = ps('VL',  'Helvetica-Bold',   9, C_BLACK, TA_LEFT)
    sty_ordine_num  = ps('ON',  'Helvetica-Bold',  13, C_DARK,  TA_LEFT)
    sty_th          = ps('TH',  'Helvetica-Bold',   8, C_WHITE, TA_CENTER)
    sty_td          = ps('TD',  'Helvetica',         8, C_BLACK, TA_LEFT)
    sty_td_r        = ps('TDR', 'Helvetica',         8, C_BLACK, TA_RIGHT)
    sty_td_bold     = ps('TDB', 'Helvetica-Bold',   8, C_BLACK, TA_RIGHT)
    sty_footer      = ps('FT',  'Helvetica',         7, C_GREY,  TA_CENTER)
    sty_note_lbl    = ps('NL',  'Helvetica-Bold',   8, C_GREY,  TA_LEFT)
    sty_note_val    = ps('NV',  'Helvetica',         8, C_BLACK, TA_LEFT)
    sty_totale      = ps('TOT', 'Helvetica-Bold',  11, C_DARK,  TA_RIGHT)

    # ── Buffer + documento ────────────────────────────────────────────────────
    buf = io.BytesIO()
    W, H = A4
    margin = 1.8 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin)
    story = []

    # ── HEADER — logo ARTEDILE  |  dati ordine ───────────────────────────────
    # Logo box (sinistra): tabella interna con sfondo gradiente simulato
    logo_inner = Table(
        [[Paragraph('<font color="#818cf8">ARTE</font><font color="#ffffff">DILE</font>',
                    sty_brand_big)],
         [Paragraph('&#9656;  Impianti ed Edilizia', sty_brand_sub)]],
        colWidths=[7.5 * cm], rowHeights=[1.9 * cm, 0.75 * cm]
    )
    logo_inner.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#0d1117')),
        ('TOPPADDING',    (0, 0), (0, 0),  14),
        ('BOTTOMPADDING', (0, 1), (0, 1),  13),
        ('LEFTPADDING',   (0, 0), (-1, -1), 18),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 18),
        ('ROUNDEDCORNERS', [10]),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW',     (0, 0), (0, 0),  0.8, colors.HexColor('#818cf8')),
    ]))

    # Numero ordine sotto il logo
    ord_num = Paragraph(f'ORDINE  ORD-{ordine.id:04d}', sty_ordine_num)
    data_ord = Paragraph(ordine.creato_il.strftime('%d %B %Y').upper(), ps('DT', 'Helvetica', 8, C_GREY))

    logo_col = Table(
        [[logo_inner], [Spacer(1, 6)], [ord_num], [data_ord]],
        colWidths=[7 * cm]
    )
    logo_col.setStyle(TableStyle([('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0), ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 2)]))

    # Dati riferimento (destra)
    def ref_row(label, value):
        return [Paragraph(label, sty_label), Paragraph(str(value or '—'), sty_value)]

    ref_data = [
        ref_row('CLIENTE',       ordine.cliente.nome if ordine.cliente else '—'),
        ref_row('AZIENDA',       ordine.azienda),
        ref_row('P.IVA',         ordine.piva),
        ref_row('RIF. ORDINE',   ordine.rif_ordine),
        ref_row('CANTIERE',      ordine.dati_cantiere),
        ref_row('INDIRIZZO',     ordine.indirizzo),
        ref_row('RIF. CANTIERE', ordine.rif_cantiere),
    ]
    ref_table = Table(ref_data, colWidths=[2.8 * cm, 6.6 * cm])
    ref_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW',    (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e7ff')),
    ]))

    # Header a due colonne
    header = Table(
        [[logo_col, ref_table]],
        colWidths=[8 * cm, 9.8 * cm]
    )
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))

    story.append(header)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=12))

    # ── TABELLA VOCI ──────────────────────────────────────────────────────────
    col_w = [1.0*cm, 7.5*cm, 1.5*cm, 1.5*cm, 2.8*cm, 2.8*cm]

    table_data = [[
        Paragraph('N.',         sty_th),
        Paragraph('Descrizione',sty_th),
        Paragraph('Qtà',        sty_th),
        Paragraph('U.M.',       sty_th),
        Paragraph('Prezzo Unit.',sty_th),
        Paragraph('Totale',     sty_th),
    ]]

    for i, riga in enumerate(ordine.righe):
        bg = C_ROW if i % 2 == 1 else C_WHITE
        table_data.append([
            Paragraph(str(riga.numero), ps(f'n{i}', 'Helvetica', 8, C_GREY, TA_CENTER)),
            Paragraph(riga.descrizione or '', sty_td),
            Paragraph(f'{riga.qta:g}',         ps(f'q{i}', 'Helvetica', 8, C_BLACK, TA_CENTER)),
            Paragraph(riga.unita_misura or '',  ps(f'u{i}', 'Helvetica', 8, C_GREY,  TA_CENTER)),
            Paragraph(f'€ {riga.prezzo_unitario:,.2f}', sty_td_r),
            Paragraph(f'€ {riga.totale:,.2f}',          sty_td_bold),
        ])

    # Riga totale
    table_data.append([
        Paragraph('', sty_th),
        Paragraph('', sty_th),
        Paragraph('', sty_th),
        Paragraph('', sty_th),
        Paragraph('TOTALE ORDINE', ps('TL', 'Helvetica-Bold', 9, C_DARK, TA_RIGHT)),
        Paragraph(f'€ {ordine.totale:,.2f}', ps('TV', 'Helvetica-Bold', 11, C_ACCENT, TA_RIGHT)),
    ])

    voci_table = Table(table_data, colWidths=col_w, repeatRows=1)

    n_righe = len(ordine.righe)
    row_styles = [
        # Header
        ('BACKGROUND',   (0, 0), (-1, 0),  C_DARK),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  C_WHITE),
        ('ROWBACKGROUND',(0, 1), (-1, n_righe), [C_WHITE, C_ROW]),
        # Riga totale
        ('BACKGROUND',   (0, -1), (-1, -1), C_LIGHT),
        ('LINEABOVE',    (0, -1), (-1, -1), 1.5, C_ACCENT),
        # Padding globale
        ('TOPPADDING',   (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 7),
        ('LEFTPADDING',  (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        # Linee interne leggere
        ('INNERGRID',    (0, 1), (-1, -2), 0.3, colors.HexColor('#e0e7ff')),
        ('BOX',          (0, 0), (-1, -1), 0.5, C_MID),
        ('ROUNDEDCORNERS', [4]),
    ]
    voci_table.setStyle(TableStyle(row_styles))

    story.append(voci_table)

    # ── NOTE ─────────────────────────────────────────────────────────────────
    if ordine.note:
        story.append(Spacer(1, 14))
        note_box = Table(
            [[Paragraph('NOTE', sty_note_lbl)],
             [Paragraph(ordine.note, sty_note_val)]],
            colWidths=[doc.width]
        )
        note_box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#faf5ff')),
            ('BOX',           (0, 0), (-1, -1), 0.5, colors.HexColor('#c7d2fe')),
            ('TOPPADDING',    (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
            ('ROUNDEDCORNERS', [6]),
        ]))
        story.append(note_box)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_LIGHT, spaceAfter=6))
    story.append(Paragraph(
        f'ARTEDILE  ·  Documento generato il {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}  ·  ORD-{ordine.id:04d}',
        sty_footer
    ))

    doc.build(story)
    buf.seek(0)
    filename = f'Ordine_ARTEDILE_{ordine.id:04d}.pdf'
    return send_file(buf, mimetype='application/pdf',
                     download_name=filename, as_attachment=True)


# ── Owner ─────────────────────────────────────────────────────────────────────

@app.route('/owner')
@login_required
@ruolo_richiesto('owner')
def owner():
    ordini_in_attesa = Ordine.query.filter_by(stato='in_attesa').count()
    nodi_count = NodoOrganico.query.count()
    soglia_corrente = get_soglia_corrente()
    n_bolle_da_approvare = DDT.query.filter_by(stato='in_attesa_tecnico').count()
    return render_template('owner.html', ordini_in_attesa=ordini_in_attesa,
                           nodi_count=nodi_count, soglia_corrente=soglia_corrente,
                           n_bolle_da_approvare=n_bolle_da_approvare, **ctx())


@app.route('/owner/ordini-in-attesa')
@login_required
@ruolo_richiesto('owner')
def owner_ordini():
    ordini = Ordine.query.filter_by(stato='in_attesa').order_by(Ordine.creato_il.desc()).all()
    return render_template('owner_ordini.html', ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@app.route('/owner/ordine/<int:ordine_id>/approva', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def approva_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('owner_ordini'))
    ordine.stato = 'approvato'
    db.session.commit()
    flash(f'Ordine #{ordine.id} approvato.', 'success')
    return redirect(url_for('owner_ordini'))


@app.route('/owner/ordine/<int:ordine_id>/rifiuta', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def rifiuta_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('owner_ordini'))
    ordine.stato = 'rifiutato'
    db.session.commit()
    flash(f'Ordine #{ordine.id} rifiutato.', 'warning')
    return redirect(url_for('owner_ordini'))


# ── Owner – Organigramma ───────────────────────────────────────────────────────

def _build_tree(nodi_tutti):
    """Organizza i nodi in una lista di radici con figli annidati."""
    by_id = {n.id: n for n in nodi_tutti}
    radici = []
    for n in nodi_tutti:
        if n.parent_id is None:
            radici.append(n)
    return radici


@app.route('/owner/organigramma')
@login_required
@ruolo_richiesto('owner')
def owner_organigramma():
    tutti_nodi = NodoOrganico.query.order_by(NodoOrganico.id).all()

    # Converte in dizionari Python puri — elimina completamente ogni
    # coinvolgimento di SQLAlchemy durante il rendering del template Jinja2
    tutti_dict = [
        {
            'id':                 n.id,
            'nome':               n.nome,
            'posizione':          n.posizione,
            'soglia_approvazione': n.soglia_approvazione,
            'parent_id':          n.parent_id,
        }
        for n in tutti_nodi
    ]

    # Mappa parent_id → [figli], costruita in O(n) — 1 sola query SQL in totale
    children_map = {}
    for d in tutti_dict:
        children_map.setdefault(d['parent_id'], []).append(d)

    radici = children_map.get(None, [])
    soglia_corrente = get_soglia_corrente()
    return render_template('owner_organigramma.html',
                           radici=radici,
                           tutti_nodi=tutti_dict,
                           children_map=children_map,
                           soglia_corrente=soglia_corrente, **ctx())


@app.route('/owner/nodo/aggiungi', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def aggiungi_nodo():
    nome = request.form.get('nome', '').strip()
    posizione = request.form.get('posizione', '').strip()
    parent_id_str = request.form.get('parent_id', '').strip()
    soglia_str = request.form.get('soglia_approvazione', '').strip()

    if not nome:
        flash('Il nome del nodo è obbligatorio.', 'danger')
        return redirect(url_for('owner_organigramma'))

    parent_id = int(parent_id_str) if parent_id_str else None
    soglia = None
    if soglia_str:
        try:
            soglia = float(soglia_str)
        except ValueError:
            pass

    nodo = NodoOrganico(nome=nome, posizione=posizione or None,
                        parent_id=parent_id, soglia_approvazione=soglia)
    db.session.add(nodo)
    db.session.commit()
    flash(f'Nodo "{nome}" aggiunto all\'organigramma.', 'success')
    return redirect(url_for('owner_organigramma'))


@app.route('/owner/nodo/<int:nodo_id>/modifica', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def modifica_nodo(nodo_id):
    nodo = db.session.get(NodoOrganico, nodo_id)
    if not nodo:
        flash('Nodo non trovato.', 'danger')
        return redirect(url_for('owner_organigramma'))
    nome = request.form.get('nome', '').strip()
    posizione = request.form.get('posizione', '').strip()
    soglia_str = request.form.get('soglia_approvazione', '').strip()
    if not nome:
        flash('Il nome è obbligatorio.', 'danger')
        return redirect(url_for('owner_organigramma'))
    nodo.nome = nome
    nodo.posizione = posizione or None
    nodo.soglia_approvazione = float(soglia_str) if soglia_str else None
    db.session.commit()
    flash(f'Nodo "{nome}" modificato.', 'success')
    return redirect(url_for('owner_organigramma'))


@app.route('/owner/nodo/<int:nodo_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def elimina_nodo(nodo_id):
    nodo = db.session.get(NodoOrganico, nodo_id)
    if not nodo:
        flash('Nodo non trovato.', 'danger')
        return redirect(url_for('owner_organigramma'))
    # Sgancia i figli (diventano radici)
    for figlio in nodo.figli:
        figlio.parent_id = None
    nome = nodo.nome
    db.session.delete(nodo)
    db.session.commit()
    flash(f'Nodo "{nome}" eliminato. I nodi figli sono stati spostati al livello radice.', 'info')
    return redirect(url_for('owner_organigramma'))


# ── Amministrazione ───────────────────────────────────────────────────────────

@app.route('/amministrazione')
@login_required
@ruolo_richiesto('amministrazione')
def amministrazione():
    fatture_attive = Fattura.query.filter_by(tipo='attiva').order_by(Fattura.data_scadenza).all()
    fatture_passive = Fattura.query.filter_by(tipo='passiva').order_by(Fattura.data_scadenza).all()
    tot_attive = sum(f.importo for f in fatture_attive)
    tot_passive = sum(f.importo for f in fatture_passive)
    tot_attive_np = sum(f.importo for f in fatture_attive if not f.pagata)
    tot_passive_np = sum(f.importo for f in fatture_passive if not f.pagata)
    oggi = date.today()
    clienti = Cliente.query.order_by(Cliente.nome).all()
    # Mappa numero_cliente → {numero_commessa → nome} per suggerimenti codice
    commesse_map = {}
    for cl in clienti:
        if cl.numero:
            commesse_map[cl.numero] = {c.numero: c.nome for c in cl.commesse}
    commesse_flat = [
        {'id': c.id,
         'nome': f"{c.codice} — {c.nome}",
         'cliente_nome': c.cliente.nome if c.cliente else ''}
        for cl in clienti for c in cl.commesse
    ]
    return render_template('amministrazione.html',
                           fatture_attive=fatture_attive,
                           fatture_passive=fatture_passive,
                           tot_attive=tot_attive, tot_passive=tot_passive,
                           tot_attive_np=tot_attive_np, tot_passive_np=tot_passive_np,
                           oggi=oggi, clienti=clienti, commesse_map=commesse_map,
                           commesse_flat=commesse_flat, **ctx())


@app.route('/amministrazione/carica-fattura', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def carica_fattura():
    tipo              = request.form.get('tipo', '')
    sottotipo         = request.form.get('sottotipo', '') if tipo == 'passiva' else None
    numero            = request.form.get('numero', '').strip()
    importo_str       = request.form.get('importo', '0').replace(',', '.')
    importo_netto_str = request.form.get('importo_netto', '').replace(',', '.')
    cliente_fornitore = request.form.get('cliente_fornitore', '').strip()
    data_emissione_str = request.form.get('data_emissione', '')
    data_scadenza_str  = request.form.get('data_scadenza', '')
    note              = request.form.get('note', '').strip()
    # Campi per auto-assegnazione cliente
    cliente_id_str    = request.form.get('cliente_id', '').strip()
    partita_iva_cl    = request.form.get('partita_iva_cliente', '').strip()
    partita_iva_forn_form = request.form.get('partita_iva_fornitore', '').strip()
    righe_json        = request.form.get('righe_json', '[]').strip()
    # Campi codice CLI/COM per flusso approvazione tecnico
    codice_cli_ref    = request.form.get('codice_cliente_ref', '').strip()
    codice_com_ref    = request.form.get('codice_commessa_ref', '').strip()

    if tipo not in ('attiva', 'passiva'):
        flash('Tipo fattura non valido.', 'danger')
        return redirect(url_for('amministrazione'))

    try:
        importo = float(importo_str)
    except ValueError:
        importo = 0.0
    try:
        importo_netto_val = float(importo_netto_str) if importo_netto_str else None
    except ValueError:
        importo_netto_val = None

    def parse_date(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        except ValueError:
            return None

    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, 'fatture')

    # P.IVA fornitore per fatture passive (usata per matching DDT)
    # Prima usa il campo dedicato partita_iva_fornitore, poi fallback a partita_iva_cliente
    piva_fornitore = (partita_iva_forn_form or partita_iva_cl) if tipo == 'passiva' else None

    fattura = Fattura(
        tipo=tipo, sottotipo=sottotipo, numero=numero, importo=importo,
        importo_netto=importo_netto_val,
        cliente_fornitore=cliente_fornitore,
        data_emissione=parse_date(data_emissione_str),
        data_scadenza=parse_date(data_scadenza_str),
        note=note, file_path=file_path,
        stato_approvazione='approvato',  # default; diventa pending per passive con codice
        codice_cliente_ref=codice_cli_ref or None,
        codice_commessa_ref=codice_com_ref or None,
        partita_iva_fornitore=piva_fornitore or None,
    )
    db.session.add(fattura)
    db.session.flush()

    # ── Assegnazione cliente / commessa (sia attiva che passiva) ─────────────
    cliente_match = None
    commessa_match = None

    # PRIORITÀ 1: codice CLI + COM inseriti manualmente → flusso approvazione tecnico
    if tipo == 'passiva' and codice_cli_ref and codice_com_ref:
        try:
            n_cli = int(codice_cli_ref)
            n_com = int(codice_com_ref)
            cli_by_code = Cliente.query.filter_by(numero=n_cli).first()
            if cli_by_code:
                com_by_code = Commessa.query.filter_by(
                    cliente_id=cli_by_code.id, numero=n_com
                ).first()
                if com_by_code:
                    cliente_match  = cli_by_code
                    commessa_match = com_by_code
                    fattura.cliente_id   = cli_by_code.id
                    fattura.commessa_id  = com_by_code.id
                    fattura.stato_approvazione = 'pending'
        except (ValueError, TypeError):
            pass

    # PRIORITÀ 1.5: commessa_id diretta (auto-rilevata da leggi_fattura via P.IVA/DDT)
    # Questo copre il caso in cui il server ha suggerito la commessa ma CLI/COM non
    # sono stati inseriti (es. cliente senza numero nel DB).
    commessa_id_form = request.form.get('commessa_id', '').strip()
    if not commessa_match and commessa_id_form and tipo == 'passiva':
        try:
            _comm_direct = db.session.get(Commessa, int(commessa_id_form))
            if _comm_direct:
                commessa_match = _comm_direct
                if not cliente_match and _comm_direct.cliente_id:
                    cliente_match = db.session.get(Cliente, _comm_direct.cliente_id)
                fattura.commessa_id = _comm_direct.id
                if cliente_match:
                    fattura.cliente_id = cliente_match.id
        except (ValueError, TypeError):
            pass

    # PRIORITÀ 2: match via P.IVA / dropdown (solo se non già matched)
    if not cliente_match:
        if cliente_id_str:
            try:
                cliente_match = db.session.get(Cliente, int(cliente_id_str))
            except (ValueError, TypeError):
                pass
        if not cliente_match and partita_iva_cl:
            cliente_match = Cliente.query.filter_by(partita_iva=partita_iva_cl).first()

        if cliente_match:
            fattura.cliente_id = cliente_match.id
            if commessa_id_form and not commessa_match:
                try:
                    commessa_match = Commessa.query.filter_by(
                        id=int(commessa_id_form), cliente_id=cliente_match.id
                    ).first()
                except (ValueError, TypeError):
                    pass
            if not commessa_match:
                commessa_match = Commessa.query.filter_by(
                    cliente_id=cliente_match.id
                ).order_by(Commessa.numero).first()
            if commessa_match:
                fattura.commessa_id = commessa_match.id

    # ── Multi-commessa: splits espliciti da PDF ─────────────────────────────
    multi_commessa_flag = request.form.get('multi_commessa', '') == '1'
    if tipo == 'passiva' and multi_commessa_flag:
        _splits_raw = request.form.get('commesse_splits_json', '[]')
        try:
            _splits = json.loads(_splits_raw)
        except Exception:
            _splits = []
        _data_c = parse_date(data_scadenza_str) or date.today()
        _cat    = 'subappalti' if sottotipo == 'subappalto' else 'materiali'
        for _sp in _splits:
            _cid = None
            try:
                _cid = int(_sp.get('commessa_id') or 0) or None
            except (TypeError, ValueError):
                pass
            if not _cid:
                continue
            _comm_sp = db.session.get(Commessa, _cid)
            if not _comm_sp:
                continue
            _tot_sp = float(_sp.get('totale') or 0)
            _desc_sp = (f"Fattura {numero} – {cliente_fornitore or ''} "
                        f"[{_sp.get('codice_raw','')}]").strip(' –[]')[:300]
            # Crea un CostoFornitore per questa commessa
            _voce_sp = CostoFornitore(
                commessa_id=_comm_sp.id,
                cliente_id=_comm_sp.cliente_id,
                categoria=_cat,
                descrizione=_desc_sp,
                importo=_tot_sp,
                data=_data_c,
            )
            db.session.add(_voce_sp)
        db.session.commit()
        flash(f'Fattura multi-commessa caricata: {len(_splits)} sezioni registrate.', 'success')
        return redirect(url_for('amministrazione'))

    # ── Auto-creazione costi (solo fatture passive NON in pending) ────────────
    costi_creati = 0
    if tipo == 'passiva' and cliente_match and fattura.stato_approvazione != 'pending':
        try:
            righe = json.loads(righe_json) if righe_json else []
        except Exception:
            righe = []
        data_costo = parse_date(data_scadenza_str) or date.today()
        cat = 'subappalti' if sottotipo == 'subappalto' else 'materiali'
        for riga in righe:
            desc = (riga.get('descrizione') or '').strip()
            if not desc:
                continue
            imp_r = riga.get('importo') or 0.0
            try:
                imp_r = float(imp_r)
            except (TypeError, ValueError):
                imp_r = 0.0
            qta = riga.get('quantita')
            pu  = riga.get('prezzo_unitario')
            voce = CostoFornitore(
                commessa_id=commessa_match.id if commessa_match else None,
                cliente_id=cliente_match.id,
                categoria=cat,
                descrizione=desc[:300],
                quantita=qta,
                valore_unitario=pu,
                importo=imp_r,
                data=data_costo,
            )
            db.session.add(voce)
            costi_creati += 1
        if costi_creati == 0 and importo > 0:
            fornitore = cliente_fornitore or cliente_match.nome
            desc_fb = f'Fattura {numero} – {fornitore}'.strip(' –')[:300]
            voce = CostoFornitore(
                commessa_id=commessa_match.id if commessa_match else None,
                cliente_id=cliente_match.id,
                categoria=cat,
                descrizione=desc_fb,
                quantita=None,
                valore_unitario=None,
                importo=importo,
                data=data_costo,
            )
            db.session.add(voce)
            costi_creati += 1

    db.session.commit()

    # ── Abbinamento + BLOCCO HARD subappalto > ordine ───────────────────────
    if tipo == 'passiva' and sottotipo == 'subappalto' and commessa_match \
            and fattura.stato_approvazione != 'pending':
        _stato_b3, _, _, _ord_b3 = _abbina_ordine_subappalto(
            fattura, commessa_match, current_user.id, solo_check=True
        )
        if _stato_b3 == 'allarme' and _ord_b3:
            _delta_b3 = (fattura.importo or 0) - (_ord_b3.totale or 0)
            fattura.stato_approvazione = 'rifiutato'
            fattura.motivo_rifiuto = (
                f'IMPORTO SUPERIORE ALL\'ORDINE — NON APPROVABILE. '
                f'Fattura: € {fattura.importo:,.2f} | '
                f'Ordine ({_ord_b3.identificativo or _ord_b3.id}): € {_ord_b3.totale:,.2f} | '
                f'Eccedenza: € {_delta_b3:,.2f}'
            )
            db.session.commit()
            try:
                _abbina_ordine_subappalto(fattura, commessa_match, current_user.id)
                db.session.commit()
            except Exception:
                pass
            flash(
                f'❌ FATTURA BLOCCATA: importo (€ {fattura.importo:,.2f}) superiore '
                f'all\'ordine {_ord_b3.identificativo or _ord_b3.id} '
                f'(€ {_ord_b3.totale:,.2f}). NON APPROVABILE — eccedenza € {_delta_b3:,.2f}.',
                'danger'
            )
            return redirect(url_for('amministrazione'))
        elif _stato_b3 == 'ok':
            try:
                _abbina_ordine_subappalto(fattura, commessa_match, current_user.id)
                db.session.commit()
            except Exception:
                pass

    if fattura.stato_approvazione == 'pending':
        flash(
            f'Fattura passiva inviata al Tecnico per approvazione '
            f'(commessa {commessa_match.nome if commessa_match else "—"}).',
            'info'
        )
    elif costi_creati:
        flash(
            f'Fattura passiva caricata e {costi_creati} voc'
            f'{"i" if costi_creati > 1 else "e"} aggiunt'
            f'{"e" if costi_creati > 1 else "a"} automaticamente nei costi del cliente.',
            'success'
        )
    else:
        flash(f'Fattura {tipo} caricata con successo.', 'success')

    return redirect(url_for('amministrazione'))


@app.route('/amministrazione/fattura/<int:fattura_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def elimina_fattura(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('amministrazione'))
    # Cascade: rimuovi abbinamenti DDT ↔ Fattura e resetta stato DDT
    for abb in AbbinatoDdtFattura.query.filter_by(fattura_id=fattura.id).all():
        ddt_linked = db.session.get(DDT, abb.ddt_id)
        if ddt_linked:
            ddt_linked.fattura_id = None
            ddt_linked.stato = 'non_matchato'
        db.session.delete(abb)
    # Cascade: rimuovi righe fattura attiva collegate
    RigaFatturaAttiva.query.filter_by(fattura_id=fattura.id).delete()
    elimina_file(fattura.file_path)
    db.session.delete(fattura)
    db.session.commit()
    flash('Fattura eliminata (abbinamenti DDT ripristinati).', 'info')
    return redirect(url_for('amministrazione'))


@app.route('/uploads/<path:filepath>')
@login_required
def serve_upload(filepath):
    directory = os.path.join(UPLOAD_ROOT, os.path.dirname(filepath))
    filename = os.path.basename(filepath)
    return send_from_directory(directory, filename)


@app.route('/amministrazione/leggi-fattura', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def leggi_fattura():
    """Riceve un file (PDF o immagine) via AJAX, estrae i dati e restituisce JSON."""
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file ricevuto'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': 'File non valido'}), 400

    try:
        content = f.read()
        fname   = f.filename.lower()
        testo   = ''
        scansione = False
        righe   = []

        IMG_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif')
        is_pdf  = fname.endswith('.pdf')
        is_img  = any(fname.endswith(e) for e in IMG_EXT)

        if is_pdf:
            # ── PDF nativo: estrai testo con pdfplumber ──────────────────────
            if PDF_SUPPORT:
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        testo += (page.extract_text() or '') + '\n'
                if testo.strip():
                    righe = estrai_righe_fattura(content)
                else:
                    # PDF scansionato (solo immagini, nessun testo selezionabile)
                    scansione = True
                    # Prova OCR se disponibile
                    if OCR_SUPPORT:
                        try:
                            from PIL import Image
                            import pytesseract
                            with pdfplumber.open(io.BytesIO(content)) as pdf:
                                for page in pdf.pages:
                                    img = page.to_image(resolution=200).original
                                    testo += pytesseract.image_to_string(img, lang='ita+eng') + '\n'
                            if testo.strip():
                                scansione = False
                        except Exception:
                            pass
            else:
                scansione = True

        elif is_img:
            # ── Immagine: prova OCR se disponibile ──────────────────────────
            if OCR_SUPPORT:
                try:
                    from PIL import Image
                    import pytesseract
                    img = Image.open(io.BytesIO(content))
                    # Pre-processo: converti in L (scala di grigi) per OCR più preciso
                    img = img.convert('L')
                    testo = pytesseract.image_to_string(img, lang='ita+eng',
                                                        config='--psm 3 --oem 3')
                    if not testo.strip():
                        scansione = True
                except Exception:
                    scansione = True
            else:
                # Nessun OCR disponibile → compilazione manuale
                scansione = True

        else:
            # Documento Word o altro testo → prova decodifica UTF-8
            try:
                testo = content.decode('utf-8', errors='ignore')
            except Exception:
                scansione = True

        dati = estrai_dati_fattura(testo)
        # dati['importo']       = valore più alto   → banca
        # dati['importo_netto'] = secondo più alto  → tecnico
        dati['scansione'] = scansione
        dati['righe']     = righe

        # Cerca match P.IVA con clienti nel database
        if dati.get('partita_iva'):
            cliente = Cliente.query.filter_by(partita_iva=dati['partita_iva']).first()
            if cliente:
                dati['cliente_match_id']   = cliente.id
                dati['cliente_match_nome'] = cliente.nome

        # Per fattura passiva: cerca fornitore e suggerisci commessa via DDT con stessa P.IVA
        if dati.get('partita_iva'):
            from collections import Counter as _Counter
            _pn = dati['partita_iva'].strip().upper().lstrip('IT').lstrip('0')
            # Match fornitore
            _forn = next(
                (fo for fo in Fornitore.query.all()
                 if fo.partita_iva and fo.partita_iva.strip().upper().lstrip('IT').lstrip('0') == _pn),
                None
            )
            if _forn:
                dati['fornitore_match_id']   = _forn.id
                dati['fornitore_match_nome'] = _forn.nome
            # Suggerisci commessa tramite DDT già caricati con questa P.IVA
            _ddts = [d for d in DDT.query.filter(DDT.commessa_id != None).all()
                     if d.partita_iva and d.partita_iva.strip().upper().lstrip('IT').lstrip('0') == _pn]
            # Fallback: cerca anche fra fatture passive già caricate con stessa P.IVA fornitore
            _fatt_comm = []
            if not _ddts:
                _fatt_prev = Fattura.query.filter(
                    Fattura.tipo == 'passiva',
                    Fattura.partita_iva_fornitore != None,
                    Fattura.commessa_id != None
                ).all()
                _fatt_comm = [f for f in _fatt_prev
                              if f.partita_iva_fornitore and
                              f.partita_iva_fornitore.strip().upper().lstrip('IT').lstrip('0') == _pn]
            if _ddts or _fatt_comm:
                if _ddts:
                    _cc = _Counter(d.commessa_id for d in _ddts)
                else:
                    _cc = _Counter(f.commessa_id for f in _fatt_comm)
                _best_id = _cc.most_common(1)[0][0]
                _comm = db.session.get(Commessa, _best_id)
                if _comm:
                    dati['commessa_suggerita_id']        = _comm.id
                    dati['commessa_suggerita_nome']      = f"{_comm.codice} — {_comm.nome}"
                    dati['commessa_suggerita_cliente_id'] = _comm.cliente_id
                    dati['commessa_suggerita_cli_num']   = _comm.cliente.numero if _comm.cliente else None
                    dati['commessa_suggerita_com_num']   = _comm.numero

        # ── Rilevamento multi-commessa (fatture passive con più sezioni) ──────
        if dati.get('sottotipo') != 'subappalto':
            _sezioni = _estrai_sezioni_commessa(testo)
            if len(_sezioni) >= 2:
                dati['multi_commessa'] = True
                dati['commesse_splits'] = [
                    {
                        'codice_raw':    _s['codice_raw'],
                        'commessa_id':   _s['commessa_id'],
                        'commessa_nome': _s['commessa_nome'],
                        'cliente_nome':  _s['cliente_nome'],
                        'totale':        _s['totale'],
                        'righe':         _s['righe'],
                    }
                    for _s in _sezioni
                ]

        return jsonify(dati)

    except Exception as e:
        return jsonify({'error': str(e), 'sottotipo': 'materiali', 'righe': []}), 200


# ── Banca ─────────────────────────────────────────────────────────────────────

@app.route('/banca')
@login_required
@ruolo_richiesto('banca')
def banca():
    BUCKETS = ['oggi', '5gg', '10gg', '20gg', '30gg', '60gg', '90gg', '120gg', '150gg']
    BUCKET_LABEL = {'oggi': 'Oggi', '5gg': '5 gg', '10gg': '10 gg', '20gg': '20 gg',
                    '30gg': '30 gg', '60gg': '60 gg', '90gg': '90 gg',
                    '120gg': '120 gg', '150gg': '150 gg'}

    fatture_attive_np = Fattura.query.filter_by(tipo='attiva', pagata=False).all()
    fatture_passive_np = Fattura.query.filter_by(tipo='passiva', pagata=False).all()

    timeline = {b: {'attive': 0.0, 'passive': 0.0} for b in BUCKETS}
    for f in fatture_attive_np:
        b = _scadenza_bucket(f.data_scadenza)
        if b:
            timeline[b]['attive'] += f.importo
    for f in fatture_passive_np:
        b = _scadenza_bucket(f.data_scadenza)
        if b:
            timeline[b]['passive'] += f.importo

    att_np = Fattura.query.filter_by(tipo='attiva', pagata=False).order_by(Fattura.data_scadenza).all()
    att_p = Fattura.query.filter_by(tipo='attiva', pagata=True).order_by(Fattura.data_scadenza.desc()).all()
    pas_np = Fattura.query.filter_by(tipo='passiva', pagata=False).order_by(Fattura.data_scadenza).all()
    pas_p = Fattura.query.filter_by(tipo='passiva', pagata=True).order_by(Fattura.data_scadenza.desc()).all()

    return render_template('banca.html',
                           buckets=BUCKETS, bucket_label=BUCKET_LABEL, timeline=timeline,
                           att_np=att_np, att_p=att_p, pas_np=pas_np, pas_p=pas_p,
                           oggi=date.today(), **ctx())


@app.route('/banca/fattura/<int:fattura_id>/toggle-pagamento', methods=['POST'])
@login_required
@ruolo_richiesto('banca')
def toggle_pagamento(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('banca'))
    fattura.pagata = not fattura.pagata
    db.session.commit()
    stato = 'segnata come PAGATA' if fattura.pagata else 'riportata a NON PAGATA'
    flash(f'Fattura {stato}.', 'success')
    return redirect(url_for('banca'))


@app.route('/tecnico/fattura/<int:fattura_id>/toggle-pagamento', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def toggle_pagamento_tecnico(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('tecnico'))
    fattura.pagata = not fattura.pagata
    db.session.commit()
    stato = 'segnata come PAGATA' if fattura.pagata else 'riportata a NON PAGATA'
    flash(f'Fattura {stato}.', 'success')
    tab = 'subappalti' if fattura.sottotipo == 'subappalto' else 'materiali'
    if fattura.commessa_id:
        return redirect(url_for('commessa_detail', commessa_id=fattura.commessa_id, tab=tab))
    if fattura.cliente_id:
        cliente = db.session.get(Cliente, fattura.cliente_id)
        if cliente and cliente.commesse:
            return redirect(url_for('commessa_detail', commessa_id=cliente.commesse[0].id, tab=tab))
    return redirect(url_for('tecnico'))


# ── Risorse Umane ─────────────────────────────────────────────────────────────

@app.route('/risorse-umane')
@login_required
@ruolo_richiesto('risorse_umane')
def risorse_umane():
    oggi = date.today()
    dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
    alert_corsi = {}
    for d in dipendenti:
        corsi_in_scadenza = DocumentoDipendente.query.filter(
            DocumentoDipendente.dipendente_id == d.id,
            DocumentoDipendente.tipo == 'corso',
            DocumentoDipendente.data_scadenza != None,
            DocumentoDipendente.data_scadenza >= oggi,
            DocumentoDipendente.data_scadenza <= oggi + timedelta(days=ALERT_SCADENZA_GIORNI)
        ).count()
        alert_corsi[d.id] = corsi_in_scadenza
    return render_template('risorse_umane.html', dipendenti=dipendenti, alert_corsi=alert_corsi, **ctx())


@app.route('/risorse-umane/nuovo-dipendente', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def nuovo_dipendente():
    nome = request.form.get('nome', '').strip()
    cognome = request.form.get('cognome', '').strip()
    if not nome or not cognome:
        flash('Nome e cognome sono obbligatori.', 'danger')
        return redirect(url_for('risorse_umane'))
    db.session.add(Dipendente(nome=nome, cognome=cognome))
    db.session.commit()
    flash(f'Dipendente {nome} {cognome} aggiunto.', 'success')
    return redirect(url_for('risorse_umane'))


@app.route('/risorse-umane/elimina-dipendente/<int:dipendente_id>', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def elimina_dipendente(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))
    docs = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id).all()
    for doc in docs:
        elimina_file(doc.nome_file)
    db.session.delete(dipendente)
    db.session.commit()
    flash(f'Dipendente {dipendente.nome} {dipendente.cognome} eliminato.', 'info')
    return redirect(url_for('risorse_umane'))


@app.route('/risorse-umane/dipendente/<int:dipendente_id>')
@login_required
@ruolo_richiesto('risorse_umane')
def dipendente_dettaglio(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))

    oggi = date.today()
    # Auto-elimina corsi scaduti
    corsi_scaduti = DocumentoDipendente.query.filter(
        DocumentoDipendente.dipendente_id == dipendente_id,
        DocumentoDipendente.tipo == 'corso',
        DocumentoDipendente.data_scadenza != None,
        DocumentoDipendente.data_scadenza < oggi
    ).all()
    eliminati = 0
    for corso in corsi_scaduti:
        elimina_file(corso.nome_file)
        db.session.delete(corso)
        eliminati += 1
    if eliminati:
        db.session.commit()
        flash(f'{eliminati} corso/i scaduto/i eliminato/i automaticamente.', 'warning')

    documenti = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id, tipo='documento').order_by(DocumentoDipendente.creato_il.desc()).all()
    corsi = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id, tipo='corso').order_by(DocumentoDipendente.data_scadenza).all()

    corsi_in_scadenza = [c for c in corsi if c.data_scadenza and c.data_scadenza <= oggi + timedelta(days=ALERT_SCADENZA_GIORNI)]

    tab = request.args.get('tab', 'documenti')
    return render_template('dipendente_dettaglio.html',
                           dipendente=dipendente, documenti=documenti,
                           corsi=corsi, corsi_in_scadenza=corsi_in_scadenza,
                           oggi=oggi, tab=tab,
                           alert_giorni=ALERT_SCADENZA_GIORNI, **ctx())


@app.route('/risorse-umane/dipendente/<int:dipendente_id>/aggiungi-documento', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiungi_documento(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))

    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Il nome del documento è obbligatorio.', 'danger')
        return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab='documenti'))

    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, f'dipendenti/{dipendente_id}/documenti')

    doc = DocumentoDipendente(dipendente_id=dipendente_id, nome=nome, nome_file=file_path, tipo='documento')
    db.session.add(doc)
    db.session.commit()
    flash('Documento aggiunto.', 'success')
    return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab='documenti'))


@app.route('/risorse-umane/dipendente/<int:dipendente_id>/aggiungi-corso', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiungi_corso(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))

    nome = request.form.get('nome', '').strip()
    data_scadenza_str = request.form.get('data_scadenza', '')

    if not nome:
        flash('Il nome del corso è obbligatorio.', 'danger')
        return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab='corsi'))

    try:
        data_scadenza = datetime.strptime(data_scadenza_str, '%Y-%m-%d').date() if data_scadenza_str else None
    except ValueError:
        data_scadenza = None

    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, f'dipendenti/{dipendente_id}/corsi')

    corso = DocumentoDipendente(dipendente_id=dipendente_id, nome=nome, nome_file=file_path,
                                tipo='corso', data_scadenza=data_scadenza)
    db.session.add(corso)
    db.session.commit()
    flash('Corso aggiunto.', 'success')
    return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab='corsi'))


@app.route('/risorse-umane/documento/<int:doc_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def elimina_documento(doc_id):
    doc = db.session.get(DocumentoDipendente, doc_id)
    if not doc:
        flash('Documento non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))
    dipendente_id = doc.dipendente_id
    tab = doc.tipo if doc.tipo in ('documento', 'corso') else 'documenti'
    elimina_file(doc.nome_file)
    db.session.delete(doc)
    db.session.commit()
    flash('Eliminato con successo.', 'info')
    redirect_tab = 'documenti' if tab == 'documento' else 'corsi'
    return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab=redirect_tab))


@app.route('/risorse-umane/dipendente/<int:dipendente_id>/costo-orario', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiorna_costo_orario(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane'))
    try:
        costo = float(request.form.get('costo_orario', '0').replace(',', '.'))
    except ValueError:
        costo = 0.0
    dipendente.costo_orario = costo
    db.session.commit()
    flash(f'Costo orario aggiornato: € {costo:.2f}/ora', 'success')
    return redirect(url_for('dipendente_dettaglio', dipendente_id=dipendente_id, tab='costo'))


# ── Admin utenti ──────────────────────────────────────────────────────────────

@app.route('/admin-utenti')
@login_required
@ruolo_richiesto('admin_utenti')
def admin_utenti():
    utenti = User.query.order_by(User.creato_il.desc()).all()
    return render_template('admin_utenti.html', utenti=utenti, ruoli=RUOLI, **ctx())


@app.route('/admin/cambia-ruolo/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def cambia_ruolo(user_id):
    if user_id == current_user.id:
        flash('Non puoi modificare il tuo stesso ruolo.', 'warning')
        return redirect(url_for('admin_utenti'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin_utenti'))
    nuovo_ruolo = request.form.get('nuovo_ruolo', '')
    if nuovo_ruolo not in RUOLI:
        flash('Ruolo non valido.', 'danger')
        return redirect(url_for('admin_utenti'))
    vecchio_ruolo = user.ruolo
    user.ruolo = nuovo_ruolo
    db.session.commit()
    flash(f'Ruolo di {user.email} cambiato da "{vecchio_ruolo}" a "{nuovo_ruolo}".', 'success')
    return redirect(url_for('admin_utenti'))


@app.route('/admin/cambia-nome/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def cambia_nome(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin_utenti'))
    nuovo_nome = request.form.get('nome', '').strip()
    user.nome = nuovo_nome or None
    db.session.commit()
    flash(f'Nome di {user.email} aggiornato a "{user.nome or "—"}".', 'success')
    return redirect(url_for('admin_utenti'))


@app.route('/admin/toggle-stato/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def toggle_stato(user_id):
    if user_id == current_user.id:
        flash('Non puoi disabilitare il tuo stesso account.', 'warning')
        return redirect(url_for('admin_utenti'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin_utenti'))
    user.attivo = not user.attivo
    db.session.commit()
    stato = 'riabilitato' if user.attivo else 'disabilitato'
    flash(f'Account di {user.email} {stato} con successo.', 'success')
    return redirect(url_for('admin_utenti'))


# ── Modulo operai (PUBBLICO — nessun login richiesto) ─────────────────────────

@app.route('/operaio')
def operaio_form():
    commesse = Commessa.query.order_by(Commessa.nome).all()
    commesse_data = [
        {
            'id': c.id,
            'nome': c.nome,
            'luogo': (c.luogo or '').lower(),
            'via': (c.via or '').lower(),
            'cliente': c.cliente.nome,
            'cliente_id': c.cliente_id,
        }
        for c in commesse
    ]
    dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
    dipendenti_data = [
        {'id': d.id, 'nome': f'{d.cognome} {d.nome}', 'costo_orario': d.costo_orario or 0.0}
        for d in dipendenti
    ]
    return render_template('operaio_form.html',
                           commesse_data=commesse_data,
                           dipendenti_data=dipendenti_data,
                           oggi=datetime.utcnow().date())


@app.route('/operaio/invia', methods=['POST'])
def operaio_invia():
    nome_operaio_raw = request.form.get('nome_operaio', '').strip()
    data_str         = request.form.get('data', '')

    def _get_ctx():
        commesse = Commessa.query.order_by(Commessa.nome).all()
        commesse_data = [{'id': c.id, 'nome': c.nome, 'luogo': (c.luogo or '').lower(),
                          'via': (c.via or '').lower(), 'cliente': c.cliente.nome,
                          'cliente_id': c.cliente_id} for c in commesse]
        dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
        dipendenti_data = [{'id': d.id, 'nome': f'{d.cognome} {d.nome}',
                            'costo_orario': d.costo_orario or 0.0} for d in dipendenti]
        return commesse_data, dipendenti_data

    def _err(msg):
        cd, dd = _get_ctx()
        return render_template('operaio_form.html',
                               commesse_data=cd, dipendenti_data=dd,
                               oggi=datetime.utcnow().date(), errore=msg)

    if not nome_operaio_raw:
        return _err('Inserisci il tuo nome e cognome.')

    # ── Lookup silente del costo orario per nome ─────────────────────────────
    costo_orario = 0.0
    nome_lookup  = nome_operaio_raw.lower()
    for d in Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all():
        d_nome = f'{d.cognome} {d.nome}'.lower()
        d_inv  = f'{d.nome} {d.cognome}'.lower()
        if nome_lookup in (d_nome, d_inv) or d_nome.startswith(nome_lookup) or d_inv.startswith(nome_lookup):
            costo_orario = d.costo_orario or 0.0
            break

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()

    nome_operaio = nome_operaio_raw

    # ── Processa i 4 blocchi ore+km ──────────────────────────────────────────
    tot_ore = 0.0
    tot_km  = 0.0
    riepilogo = []

    for i in range(1, 5):
        dove_str     = request.form.get(f'slot_dove_{i}', '').strip()
        ore_str_slot = request.form.get(f'slot_ore_{i}', '').replace(',', '.')
        km_str_slot  = request.form.get(f'slot_km_{i}', '0').replace(',', '.')
        comm_id_s    = request.form.get(f'slot_commessa_id_{i}', '').strip()
        try:
            ore_slot = float(ore_str_slot) if ore_str_slot else 0.0
        except ValueError:
            ore_slot = 0.0
        try:
            km_slot = float(km_str_slot) if km_str_slot else 0.0
        except ValueError:
            km_slot = 0.0

        if ore_slot <= 0 and km_slot <= 0:
            continue

        # Risolvi commessa suggerita
        commessa = None
        if comm_id_s:
            try:
                commessa = db.session.get(Commessa, int(comm_id_s))
            except (ValueError, TypeError):
                pass

        comm_sug_id  = commessa.id if commessa else None
        cliente_id_s = commessa.cliente_id if commessa else None
        desc_base    = f'{nome_operaio} – {dove_str}' if dove_str else nome_operaio

        # Ore: stato pending, vanno al cliente (non ancora alla commessa)
        if ore_slot > 0:
            importo_ore = round(ore_slot * costo_orario, 2)
            tot_ore += ore_slot
            db.session.add(CostoFornitore(
                commessa_id=None,                       # non assegnata direttamente
                commessa_suggerita_id=comm_sug_id,      # suggerita per approvazione
                cliente_id=cliente_id_s,
                categoria='personale',
                sottocategoria='ore',
                descrizione=desc_base[:300],
                quantita=ore_slot,
                valore_unitario=costo_orario if costo_orario else None,
                importo=importo_ore,
                stato='pending',
                data=data,
            ))
            label = commessa.nome if commessa else dove_str or '—'
            riepilogo.append(f'{ore_slot}h → {label}')

        # Km: stato pending, stessa logica
        if km_slot > 0:
            tariffa_km = 0.30
            tot_km += km_slot
            db.session.add(CostoFornitore(
                commessa_id=None,
                commessa_suggerita_id=comm_sug_id,
                cliente_id=cliente_id_s,
                categoria='personale',
                sottocategoria='km',
                descrizione=f'{desc_base} – {km_slot:.0f} km',
                quantita=km_slot,
                valore_unitario=tariffa_km,
                importo=round(km_slot * tariffa_km, 2),
                stato='pending',
                data=data,
            ))

    if tot_ore <= 0 and tot_km <= 0:
        return _err('Inserisci le ore in almeno uno dei blocchi.')

    db.session.commit()

    cantiere_str = ' | '.join(riepilogo) if riepilogo else '—'
    return render_template('operaio_ok.html',
                           nome=nome_operaio,
                           cantiere=cantiere_str,
                           tot_ore=tot_ore,
                           tot_km=tot_km,
                           data=data)


# ── Ore non allocate (tecnico) ─────────────────────────────────────────────────
@app.route('/tecnico/approvazioni-ore')
@login_required
@ruolo_richiesto('tecnico')
def approvazioni_ore():
    voci = (CostoFornitore.query
            .filter_by(categoria='personale', stato='pending')
            .order_by(CostoFornitore.data.desc()).all())
    commesse_all = Commessa.query.order_by(Commessa.nome).all()
    tot_ore = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'ore')
    tot_km  = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'km')
    return render_template('approvazioni_ore.html',
                           voci=voci, commesse_all=commesse_all,
                           tot_ore=tot_ore, tot_km=tot_km, **ctx())


@app.route('/tecnico/approvazioni-ore/<int:voce_id>/azione', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def azione_approvazione_ora(voce_id):
    voce = db.session.get(CostoFornitore, voce_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('approvazioni_ore'))
    from sqlalchemy import text as _text
    azione = request.form.get('azione', '')
    if azione == 'approva':
        # ── Quantità modificata dal responsabile ────────────────────
        nuova_q_str = request.form.get('nuova_quantita', '').strip()
        try:
            nuova_q = float(nuova_q_str) if nuova_q_str else (voce.quantita or 0)
        except ValueError:
            nuova_q = voce.quantita or 0
        # ── Commessa selezionata ─────────────────────────────────────
        commessa_id_s = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_s:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_s))
            except (ValueError, TypeError):
                pass
        # fallback: commessa suggerita dall'operaio
        if not commessa:
            sug_id = voce.commessa_suggerita_id
            if sug_id:
                commessa = db.session.get(Commessa, sug_id)
        if not commessa:
            flash('Seleziona una commessa per approvare.', 'warning')
            return redirect(url_for('approvazioni_ore'))
        # Ricalcolo importo
        nuovo_importo = voce.importo or 0
        if voce.valore_unitario:
            nuovo_importo = round(nuova_q * voce.valore_unitario, 2)
        # Aggiornamento diretto via SQL per evitare problemi di cascade ORM
        db.session.execute(_text(
            "UPDATE costi_fornitori SET quantita=:q, importo=:imp, "
            "commessa_id=:cid, cliente_id=:klid, stato='approvato' "
            "WHERE id=:vid"
        ), dict(q=nuova_q, imp=nuovo_importo,
                cid=commessa.id, klid=commessa.cliente_id,
                vid=voce_id))
        db.session.commit()
        label = 'ore' if voce.sottocategoria == 'ore' else 'km'
        flash(f'{nuova_q} {label} approvati → {commessa.cliente.nome} / {commessa.nome}.', 'success')
    elif azione == 'rifiuta':
        db.session.execute(_text(
            "UPDATE costi_fornitori SET stato='rifiutato' WHERE id=:vid"
        ), dict(vid=voce_id))
        db.session.commit()
        flash('Voce rifiutata e spostata in "Ore non allocate".', 'info')
    return redirect(url_for('approvazioni_ore'))


@app.route('/tecnico/ore-non-allocate')
@login_required
@ruolo_richiesto('tecnico')
def ore_non_allocate():
    voci = (CostoFornitore.query
            .filter_by(categoria='personale', stato='rifiutato')
            .order_by(CostoFornitore.data.desc()).all())
    commesse_all = Commessa.query.order_by(Commessa.nome).all()
    tot_ore     = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'ore')
    tot_importo = sum(v.importo  or 0 for v in voci)
    return render_template('ore_non_allocate.html',
                           voci=voci, commesse_all=commesse_all,
                           tot_ore=tot_ore, tot_importo=tot_importo, **ctx())


@app.route('/tecnico/ore-non-allocate/<int:voce_id>/assegna', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def assegna_ora_non_allocata(voce_id):
    from sqlalchemy import text as _text
    commessa_id_str = request.form.get('commessa_id', '').strip()
    nuova_q_str     = request.form.get('nuova_quantita', '').strip()
    try:
        commessa = db.session.get(Commessa, int(commessa_id_str))
    except (ValueError, TypeError):
        commessa = None
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('ore_non_allocate'))
    # Leggi quantità attuale e calcola aggiornamento
    voce = db.session.get(CostoFornitore, voce_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('ore_non_allocate'))
    try:
        nuova_q = float(nuova_q_str) if nuova_q_str else (voce.quantita or 0)
    except ValueError:
        nuova_q = voce.quantita or 0
    nuovo_importo = voce.importo or 0
    if voce.valore_unitario and nuova_q:
        nuovo_importo = round(nuova_q * voce.valore_unitario, 2)
    db.session.execute(_text(
        "UPDATE costi_fornitori SET quantita=:q, importo=:imp, "
        "commessa_id=:cid, cliente_id=:klid, stato='approvato' "
        "WHERE id=:vid"
    ), dict(q=nuova_q, imp=nuovo_importo,
            cid=commessa.id, klid=commessa.cliente_id,
            vid=voce_id))
    db.session.commit()
    label = 'ore' if voce.sottocategoria == 'ore' else 'km'
    flash(f'{nuova_q} {label} assegnati → {commessa.cliente.nome} / {commessa.nome}.', 'success')
    return redirect(url_for('ore_non_allocate'))


# ════════════════════════════════════════════════════════════════════
#  Approvazioni Fatture Passive
# ════════════════════════════════════════════════════════════════════

@app.route('/tecnico/approvazioni-fatture')
@login_required
@ruolo_richiesto('tecnico')
def approvazioni_fatture():
    fatture = (Fattura.query
               .filter_by(tipo='passiva', stato_approvazione='pending')
               .order_by(Fattura.creato_il.desc())
               .all())
    commesse_all = (Commessa.query
                    .join(Cliente)
                    .order_by(Cliente.nome, Commessa.numero)
                    .all())
    tot_importo = sum(f.importo or 0 for f in fatture)
    return render_template('approvazioni_fatture.html',
                           fatture=fatture, commesse_all=commesse_all,
                           tot_importo=tot_importo, **ctx())


@app.route('/tecnico/approvazioni-fatture/<int:fattura_id>/azione', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def azione_approvazione_fattura(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('approvazioni_fatture'))
    azione = request.form.get('azione', '')
    if azione == 'approva':
        commessa_id_s = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_s:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_s))
            except (ValueError, TypeError):
                pass
        if not commessa and fattura.commessa_id:
            commessa = db.session.get(Commessa, fattura.commessa_id)
        if not commessa:
            flash('Seleziona una commessa per approvare la fattura.', 'warning')
            return redirect(url_for('approvazioni_fatture'))
        fattura.commessa_id = commessa.id
        fattura.cliente_id  = commessa.cliente_id
        # ── BLOCCO SUBAPPALTO: controlla importo vs ordine PRIMA di approvare ──
        if fattura.sottotipo == 'subappalto':
            _stato_ab, _, _, _ordine_ab = _abbina_ordine_subappalto(
                fattura, commessa, current_user.id, solo_check=True
            )
            if _stato_ab == 'allarme':
                _delta = (fattura.importo or 0) - (_ordine_ab.totale if _ordine_ab else 0)
                _msg = (
                    f"IMPORTO FATTURA (€ {fattura.importo:,.2f}) SUPERA L'ORDINE "
                    f"(€ {(_ordine_ab.totale if _ordine_ab else 0):,.2f}) — "
                    f"Δ € {_delta:,.2f}. NON APPROVABILE."
                )
                db.session.rollback()
                flash(f'❌ APPROVAZIONE BLOCCATA — {_msg}', 'danger')
                return redirect(url_for('approvazioni_fatture'))
        fattura.stato_approvazione = 'approvato'
        cat = 'subappalti' if fattura.sottotipo == 'subappalto' else 'materiali'
        # importo_netto = valore approvato dal tecnico (senza IVA)
        # importo       = totale con IVA, NON viene toccato → banca lo vede invariato
        importo_approv = fattura.importo_netto or fattura.importo or 0
        nuovo_netto_s = request.form.get('importo_netto', '').strip().replace(',', '.')
        if nuovo_netto_s:
            try:
                importo_approv = float(nuovo_netto_s)
                fattura.importo_netto = importo_approv  # aggiorna solo importo_netto
                # fattura.importo (con IVA) rimane intatto per la banca
            except ValueError:
                pass
        desc = f'Fattura {fattura.numero or ""} – {fattura.cliente_fornitore or ""}'.strip(' –')[:300]
        voce = CostoFornitore(
            commessa_id=commessa.id,
            cliente_id=commessa.cliente_id,
            categoria=cat,
            descrizione=desc,
            importo=importo_approv,
            data=fattura.data_scadenza or date.today(),
        )
        db.session.add(voce)
        db.session.commit()
        flash(f'Fattura approvata e costo registrato in {commessa.cliente.nome} / {commessa.nome}.', 'success')
        # ── Abbinamento automatico post-approvazione ──
        try:
            if fattura.sottotipo == 'subappalto':
                _s, _r, _m, _o = _abbina_ordine_subappalto(fattura, commessa, current_user.id)
                if _r:
                    db.session.commit()
                    if _s == 'ok':
                        flash(f'Ordine abbinato: {_m}', 'info')
            elif fattura.sottotipo == 'materiali':
                pass  # DDT matching avviene alla conferma del DDT
        except Exception:
            pass
        # Genera archivio ZIP per questa fattura + DDT collegati
        try:
            import zipfile as zf_mod
            archivi_dir = os.path.join(UPLOAD_ROOT, 'archivi')
            os.makedirs(archivi_dir, exist_ok=True)
            nome_zip = (f'archivio_{commessa.codice}_{fattura.numero or fattura.id}.zip'
                        .replace(' ', '_').replace('/', '-'))
            with zf_mod.ZipFile(os.path.join(archivi_dir, nome_zip), 'w', zf_mod.ZIP_DEFLATED) as z:
                if fattura.file_path:
                    fp = os.path.join(UPLOAD_ROOT, fattura.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'fatture/{os.path.basename(fp)}')
                tecnico_obj = db.session.get(User, fattura.tecnico_id) if fattura.tecnico_id else None
                ddt_folder = f'DDT/{tecnico_obj.nome}/' if tecnico_obj else 'DDT/'
                for d in DDT.query.filter_by(fattura_id=fattura.id).all():
                    if d.file_path:
                        fp = os.path.join(UPLOAD_ROOT, d.file_path)
                        if os.path.exists(fp):
                            z.write(fp, f'{ddt_folder}{os.path.basename(fp)}')
        except Exception:
            pass
    elif azione == 'rifiuta':
        motivo = request.form.get('motivo_rifiuto', '').strip()
        fattura.stato_approvazione = 'rifiutato'
        fattura.motivo_rifiuto = motivo or None
        db.session.commit()
        commessa_nome = fattura.commessa_obj.codice if fattura.commessa_obj else '—'
        flash(
            f'{current_user.nome or current_user.email} ha rifiutato la fattura '
            f'N°{fattura.numero or "—"} inerente a commessa {commessa_nome}'
            f'{(": " + motivo) if motivo else ""}.',
            'warning'
        )
    return redirect(url_for('approvazioni_fatture'))


# ── DB init & migration ───────────────────────────────────────────────────────

with app.app_context():
    db.create_all()
    os.makedirs(os.path.join(UPLOAD_ROOT, 'fatture'), exist_ok=True)
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    cols = [c['name'] for c in inspector.get_columns('users')]
    if 'attivo' not in cols:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE users ADD COLUMN attivo BOOLEAN NOT NULL DEFAULT 1'))
            conn.commit()
    # Migrazione: tabella nodi_organico
    if not inspector.has_table('nodi_organico'):
        db.create_all()
    # Migrazione: nuove colonne CostoFornitore
    cf_cols = [c['name'] for c in inspector.get_columns('costi_fornitori')]
    with db.engine.connect() as conn:
        for col, ddl in [
            ('sottocategoria',        'VARCHAR(20)'),
            ('quantita',              'FLOAT'),
            ('valore_unitario',       'FLOAT'),
            ('stato',                 "VARCHAR(20) DEFAULT 'approvato'"),
            ('commessa_suggerita_id', 'INTEGER'),
        ]:
            if col not in cf_cols:
                conn.execute(text(f'ALTER TABLE costi_fornitori ADD COLUMN {col} {ddl}'))
        conn.commit()
    # Migrazione: nuove colonne Ordine
    ord_cols = [c['name'] for c in inspector.get_columns('ordini')]
    with db.engine.connect() as conn:
        for col, ddl in [
            ('commessa_id',   'INTEGER'),
            ('identificativo', 'VARCHAR(50)'),
            ('cig',           'VARCHAR(200)'),
            ('cup',           'VARCHAR(200)'),
        ]:
            if col not in ord_cols:
                conn.execute(text(f'ALTER TABLE ordini ADD COLUMN {col} {ddl}'))
        conn.commit()
    # Migrazione: colonna nome su users
    u_cols = [c['name'] for c in inspector.get_columns('users')]
    if 'nome' not in u_cols:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE users ADD COLUMN nome VARCHAR(200)'))
            conn.commit()
    # Migrazione: partita_iva + owner + numero + cig + cup su clienti
    cl_cols = [c['name'] for c in inspector.get_columns('clienti')]
    with db.engine.connect() as conn:
        for col, ddl in [
            ('partita_iva', 'VARCHAR(30)'),
            ('owner',       'VARCHAR(200)'),
            ('numero',      'INTEGER'),
            ('cig',         'VARCHAR(100)'),
            ('cup',         'VARCHAR(100)'),
        ]:
            if col not in cl_cols:
                conn.execute(text(f'ALTER TABLE clienti ADD COLUMN {col} {ddl}'))
        conn.commit()
    # Assegna numero progressivo ai clienti esistenti senza numero
    for i, cl in enumerate(Cliente.query.filter(Cliente.numero == None).order_by(Cliente.id).all(), start=1):
        max_n = db.session.query(db.func.max(Cliente.numero)).scalar() or 0
        cl.numero = max_n + 1
    db.session.commit()
    # Migrazione: tabella commesse
    if not inspector.has_table('commesse'):
        db.create_all()
    # Migrazione: commessa_id su costi_fornitori
    cf_cols2 = [c['name'] for c in inspector.get_columns('costi_fornitori')]
    if 'commessa_id' not in cf_cols2:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE costi_fornitori ADD COLUMN commessa_id INTEGER'))
            conn.commit()
    # Migrazione: cliente_id + commessa_id + approvazione su fatture
    ft_cols = [c['name'] for c in inspector.get_columns('fatture')]
    with db.engine.connect() as conn:
        for col, ddl in [
            ('cliente_id',          'INTEGER'),
            ('commessa_id',         'INTEGER'),
            ('stato_approvazione',  "VARCHAR(20) DEFAULT 'approvato'"),
            ('codice_cliente_ref',  'VARCHAR(20)'),
            ('codice_commessa_ref', 'VARCHAR(20)'),
        ]:
            if col not in ft_cols:
                conn.execute(text(f'ALTER TABLE fatture ADD COLUMN {col} {ddl}'))
        conn.commit()
    # Migrazione: tabella app_config
    if not inspector.has_table('app_config'):
        db.create_all()
    # Migrazione: nuove colonne Fattura (tecnico_id, motivo_rifiuto)
    ft_cols2 = [c['name'] for c in inspector.get_columns('fatture')]
    with db.engine.connect() as conn:
        for col, ddl in [
            ('tecnico_id',     'INTEGER'),
            ('motivo_rifiuto', 'TEXT'),
        ]:
            if col not in ft_cols2:
                conn.execute(text(f'ALTER TABLE fatture ADD COLUMN {col} {ddl}'))
        conn.commit()
    # Migrazione: tabella DDT
    if not inspector.has_table('ddt'):
        db.create_all()
    else:
        ddt_cols = [c['name'] for c in inspector.get_columns('ddt')]
        with db.engine.connect() as conn:
            for col, ddl in [
                ('file_path',          'VARCHAR(500)'),
                ('tecnico_id',         'INTEGER'),
                ('motivo_rifiuto',     'TEXT'),
                ('indirizzo_cantiere', 'VARCHAR(500)'),
            ]:
                if col not in ddt_cols:
                    conn.execute(text(f'ALTER TABLE ddt ADD COLUMN {col} {ddl}'))
            conn.commit()
    # Migrazione dati: crea commessa di default per clienti esistenti senza commesse
    clienti_tutti = Cliente.query.all()
    migrated = False
    for cl in clienti_tutti:
        if not Commessa.query.filter_by(cliente_id=cl.id).first():
            max_n = db.session.query(db.func.max(Commessa.numero)).filter_by(cliente_id=cl.id).scalar() or 0
            c = Commessa(cliente_id=cl.id, numero=max_n + 1, nome='Commessa principale')
            db.session.add(c)
            db.session.flush()
            # Sposta costi esistenti
            with db.engine.connect() as conn:
                conn.execute(text(
                    'UPDATE costi_fornitori SET commessa_id=:cid WHERE cliente_id=:lid AND commessa_id IS NULL'
                ), {'cid': c.id, 'lid': cl.id})
                conn.execute(text(
                    "UPDATE fatture SET commessa_id=:cid WHERE cliente_id=:lid AND commessa_id IS NULL AND tipo='passiva'"
                ), {'cid': c.id, 'lid': cl.id})
                conn.commit()
            migrated = True
    if migrated:
        db.session.commit()
    # Migrazione: colonna partita_iva_fornitore su fatture
    ft_cols3 = [c['name'] for c in inspector.get_columns('fatture')]
    if 'partita_iva_fornitore' not in ft_cols3:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE fatture ADD COLUMN partita_iva_fornitore VARCHAR(30)'))
            conn.commit()
    # Migrazione: colonne extra fattura attiva strutturata
    _ft_ext = [c['name'] for c in inspector.get_columns('fatture')]
    for _col, _typ in [('piva_cliente','VARCHAR(30)'),('mia_piva','VARCHAR(30)'),
                       ('indirizzo_cliente','VARCHAR(500)'),('pec_cliente','VARCHAR(200)'),
                       ('cup','VARCHAR(50)'),('cig','VARCHAR(50)'),
                       ('aliquota_iva','FLOAT'),('importo_netto','FLOAT')]:
        if _col not in _ft_ext:
            with db.engine.connect() as _c:
                _c.execute(text(f'ALTER TABLE fatture ADD COLUMN {_col} {_typ}'))
                _c.commit()
    # Migrazione: tabella righe_fattura_attiva
    if not inspector.has_table('righe_fattura_attiva'):
        db.session.execute(text('''
            CREATE TABLE righe_fattura_attiva (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                fattura_id INTEGER NOT NULL REFERENCES fatture(id) ON DELETE CASCADE,
                articolo VARCHAR(100),
                descrizione TEXT NOT NULL,
                quantita FLOAT,
                unita_misura VARCHAR(20),
                prezzo_unitario FLOAT,
                importo_netto FLOAT
            )
        '''))
        db.session.commit()
        print('[MIGRATION] righe_fattura_attiva creata')
    # Migrazione: nuove tabelle matching
    for tbl in ['fornitori', 'abbinati_ddt_fattura', 'abbinati_ordine_subappalto']:
        if not inspector.has_table(tbl):
            db.create_all()
    # Migrazione: partita_iva su DDT
    ddt_cols2 = [c['name'] for c in inspector.get_columns('ddt')]
    if 'partita_iva' not in ddt_cols2:
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE ddt ADD COLUMN partita_iva VARCHAR(30)'))
            conn.commit()
    # Migrazione: tabella righe_ddt
    if not inspector.has_table('righe_ddt'):
        db.session.execute(text('''
            CREATE TABLE righe_ddt (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                ddt_id INTEGER NOT NULL REFERENCES ddt(id) ON DELETE CASCADE,
                commessa_id INTEGER REFERENCES commesse(id),
                codice_commessa VARCHAR(30),
                descrizione TEXT NOT NULL,
                quantita FLOAT,
                unita_misura VARCHAR(20),
                prezzo_unitario FLOAT,
                importo FLOAT
            )
        '''))
        db.session.commit()
        print('[MIGRATION] righe_ddt creata')

# ════════════════════════════════════════════════════════════════════
#  DDT – Documenti di Trasporto
# ════════════════════════════════════════════════════════════════════

def _ddt_ctx():
    """Contesto comune per le viste DDT."""
    tutti = DDT.query.filter(DDT.stato != 'in_revisione').order_by(DDT.creato_il.desc()).all()
    n_abbinati   = sum(1 for d in tutti if d.stato == 'abbinato')
    n_anomali    = sum(1 for d in tutti if d.stato in ('non_matchato', 'importo_anomalo'))
    tot_importo  = sum(d.importo or 0 for d in tutti)
    n_revisione  = DDT.query.filter_by(stato='in_revisione').count()
    n_rifiutate  = DDT.query.filter_by(stato='rifiutato_tecnico').count()
    n_ft_anomale = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).count()
    return dict(tutti=tutti, n_abbinati=n_abbinati, n_anomali=n_anomali,
                tot_importo=tot_importo, n_ft_anomale=n_ft_anomale,
                n_revisione=n_revisione, n_rifiutate=n_rifiutate,
                oggi=datetime.utcnow().date())


@app.route('/amministrazione/ddt')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_lista():
    return render_template('ddt_lista.html', ruolo='amministrativo',
                           back_url=url_for('amministrazione'),
                           anomali_url=url_for('ddt_anomali'),
                           ft_anomale_url=url_for('fatture_anomale'),
                           **_ddt_ctx(), **ctx())


@app.route('/tecnico/ddt')
@login_required
@ruolo_richiesto('tecnico')
def ddt_lista_tecnico():
    return render_template('ddt_lista.html', ruolo='tecnico',
                           back_url=url_for('tecnico'),
                           anomali_url=url_for('ddt_anomali_tecnico'),
                           ft_anomale_url=url_for('fatture_anomale_tecnico'),
                           **_ddt_ctx(), **ctx())


@app.route('/amministrazione/ddt/anomali')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_anomali():
    non_matchati    = DDT.query.filter_by(stato='non_matchato').order_by(DDT.creato_il.desc()).all()
    importo_anomalo = DDT.query.filter_by(stato='importo_anomalo').order_by(DDT.creato_il.desc()).all()
    return render_template('ddt_anomali.html',
                           non_matchati=non_matchati, importo_anomalo=importo_anomalo,
                           lista_url=url_for('ddt_lista'),
                           ft_anomale_url=url_for('fatture_anomale'), **ctx())


@app.route('/tecnico/ddt/anomali')
@login_required
@ruolo_richiesto('tecnico')
def ddt_anomali_tecnico():
    non_matchati    = DDT.query.filter_by(stato='non_matchato').order_by(DDT.creato_il.desc()).all()
    importo_anomalo = DDT.query.filter_by(stato='importo_anomalo').order_by(DDT.creato_il.desc()).all()
    return render_template('ddt_anomali.html',
                           non_matchati=non_matchati, importo_anomalo=importo_anomalo,
                           lista_url=url_for('ddt_lista_tecnico'),
                           ft_anomale_url=url_for('fatture_anomale_tecnico'), **ctx())


@app.route('/amministrazione/fatture-anomale')
@login_required
@ruolo_richiesto('amministrazione')
def fatture_anomale():
    fatture = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).order_by(Fattura.data_emissione.desc()).all()
    return render_template('fatture_anomale.html', fatture=fatture,
                           lista_url=url_for('ddt_lista'), **ctx())


@app.route('/tecnico/fatture-anomale')
@login_required
@ruolo_richiesto('tecnico')
def fatture_anomale_tecnico():
    fatture = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).order_by(Fattura.data_emissione.desc()).all()
    return render_template('fatture_anomale.html', fatture=fatture,
                           lista_url=url_for('ddt_lista_tecnico'), **ctx())


@app.route('/amministrazione/ddt/importa-gmail', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def importa_ddt_gmail():
    import imaplib
    import email as emaillib
    import email.utils as emailutils
    import uuid
    import json

    gmail_user = os.environ.get('GMAIL_USER', 'bolleddt13@gmail.com')
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD', 'wkwkybgcnnbzqlay')
    if not gmail_pass:
        flash("Password Gmail non configurata. Imposta GMAIL_APP_PASSWORD nelle variabili d'ambiente.", 'danger')
        return redirect(url_for('ddt_lista'))

    # ── Carica set di UID già processati (persistente nel DB) ──────────
    UID_KEY = 'gmail_ddt_uid_visti'
    uid_visti = set(json.loads(AppConfig.get(UID_KEY, '[]')))

    risultati = {'ok': 0, 'saltati': 0}

    def _estrai_codici(testo):
        """Wrapper: delega a _cerca_commessa_nel_testo_pdf (logica avanzata)."""
        _, cod_cl, cod_cm, _ = _cerca_commessa_nel_testo_pdf(testo)
        return cod_cl, cod_cm

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        try:
            mail.login(gmail_user, gmail_pass)
        except imaplib.IMAP4.error as login_err:
            err_msg = str(login_err)
            if 'Application-specific password required' in err_msg or 'AUTHENTICATE' in err_msg or 'Invalid credentials' in err_msg:
                flash(
                    'Gmail ha rifiutato la password. '
                    'Per usare IMAP devi generare una "App Password" di 16 caratteri dal tuo account Google: '
                    'Google Account → Sicurezza → Verifica in 2 passaggi → App password. '
                    f'Errore originale: {err_msg}',
                    'danger'
                )
            else:
                flash(f'Errore login Gmail ({gmail_user}): {err_msg}', 'danger')
            return redirect(url_for('ddt_lista'))
        mail.select('INBOX')

        # Cerca TUTTI i messaggi (non solo UNSEEN) per trovare gli allegati PDF
        # ma salta quelli già importati tramite UID
        _, search_data = mail.search(None, 'ALL')
        msg_ids = search_data[0].split()

        # Recupera gli UID IMAP (stabili anche dopo riavvii)
        nuovi_uid = set()

        for num in msg_ids:
            # Ottieni UID stabile del messaggio
            _, uid_data = mail.fetch(num, '(UID)')
            uid_str = uid_data[0].decode() if uid_data and uid_data[0] else ''
            uid_match = re.search(r'UID\s+(\d+)', uid_str)
            uid = uid_match.group(1) if uid_match else num.decode()

            if uid in uid_visti:
                risultati['saltati'] += 1
                continue

            _, msg_data = mail.fetch(num, '(RFC822)')
            msg = emaillib.message_from_bytes(msg_data[0][1])
            mittente = emailutils.parseaddr(msg.get('From', ''))[1]

            ha_pdf = False
            for part in msg.walk():
                ct = part.get_content_type()
                fn = (part.get_filename() or '').strip()
                is_pdf = (ct == 'application/pdf' or
                          fn.lower().endswith('.pdf') or
                          (ct == 'application/octet-stream' and fn.lower().endswith('.pdf')))
                if not is_pdf:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                ha_pdf = True

                # ── Salva il file ────────────────────────────────────
                safe_name  = f"{uuid.uuid4().hex}_{fn or 'ddt.pdf'}"
                upload_dir = os.path.join(UPLOAD_ROOT, 'ddt')
                os.makedirs(upload_dir, exist_ok=True)
                fpath = os.path.join(upload_dir, safe_name)
                with open(fpath, 'wb') as fp:
                    fp.write(payload)
                rel_path = os.path.join('ddt', safe_name)

                # ── Estrai dati dal PDF ──────────────────────────────
                numero_ddt         = fn.replace('.pdf', '').replace('.PDF', '') or f'Gmail-{uid}'
                fornitore_ddt      = mittente
                importo_ddt        = 0.0
                data_ddt           = datetime.utcnow().date()
                codice_cliente     = ''
                codice_commessa    = ''
                indirizzo_cantiere = ''

                try:
                    with pdfplumber.open(io.BytesIO(payload)) as pdf:
                        testo = '\n'.join(p.extract_text() or '' for p in pdf.pages)
                    dati = estrai_dati_fattura(testo)
                    numero_ddt    = dati.get('numero')            or numero_ddt
                    fornitore_ddt = dati.get('cliente_fornitore') or fornitore_ddt
                    importo_ddt   = float(dati.get('importo') or 0)
                    if dati.get('data_emissione'):
                        try:
                            data_ddt = datetime.strptime(dati['data_emissione'], '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    # Ricerca avanzata: C001-3 → etichette → codici separati
                    commessa_da_codice, codice_cliente, codice_commessa, metodo_cod = \
                        _cerca_commessa_nel_testo_pdf(testo)
                    indirizzo_cantiere = _estrai_indirizzo_cantiere(testo)
                except Exception:
                    commessa_da_codice = None

                # ── Salva come "in revisione" — matching fatto dall'utente ──
                ddt = DDT(
                    numero             = numero_ddt,
                    data               = data_ddt,
                    fornitore          = fornitore_ddt or None,
                    codice_cliente     = codice_cliente,
                    codice_commessa    = codice_commessa,
                    indirizzo_cantiere = indirizzo_cantiere or None,
                    importo            = importo_ddt,
                    commessa_id        = commessa_da_codice.id if commessa_da_codice else None,
                    fattura_id         = None,
                    stato              = 'in_revisione',
                    note_anomalia      = 'Importato da Gmail — da verificare e assegnare.',
                    file_path          = rel_path,
                    creato_da          = current_user.id,
                )
                db.session.add(ddt)
                risultati['ok'] += 1

            # Segna l'UID come visto (anche se non aveva PDF, per non riscansionarlo)
            nuovi_uid.add(uid)

        mail.close()
        mail.logout()
        db.session.commit()

        # ── Salva UID aggiornati nel DB ──────────────────────────────
        tutti_uid = uid_visti | nuovi_uid
        # Mantieni solo gli ultimi 2000 UID per evitare crescita infinita
        if len(tutti_uid) > 2000:
            tutti_uid = set(sorted(tutti_uid, key=lambda x: int(x) if x.isdigit() else 0)[-2000:])
        AppConfig.set(UID_KEY, json.dumps(list(tutti_uid)))

        tot = risultati['ok']
        if tot == 0:
            flash(
                f'Nessun nuovo DDT trovato in {gmail_user}'
                f'{(" ("+str(risultati["saltati"])+" già importati)") if risultati["saltati"] else ""}.',
                'info'
            )
        else:
            extra = f" ({risultati['saltati']} già presenti saltati)" if risultati['saltati'] else ''
            flash(
                f'{tot} DDT importati da {gmail_user}{extra}. '
                f'Aprire la sezione "In Revisione" per verificarli e assegnarli.',
                'success'
            )

    except Exception as e:
        flash(f'Errore connessione Gmail ({gmail_user}): {e}', 'danger')
    return redirect(url_for('ddt_lista'))


@app.route('/amministrazione/ddt/nuovo', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def nuovo_ddt():
    numero          = request.form.get('numero', '').strip()
    data_str        = request.form.get('data', '').strip()
    fornitore       = request.form.get('fornitore', '').strip()
    codice_cliente  = request.form.get('codice_cliente', '').strip()
    codice_commessa = request.form.get('codice_commessa', '').strip()
    descrizione     = request.form.get('descrizione', '').strip()
    try:
        importo = float(request.form.get('importo', '0').replace(',', '.'))
    except ValueError:
        importo = 0.0

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()

    # ── Allegato file ─────────────────────────────────────────────────
    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, 'ddt')

    # ── Matching automatico ──────────────────────────────────────────
    commessa, fattura_ref, stato, note_anomalia = _match_ddt(codice_cliente, codice_commessa, importo)

    ddt = DDT(
        numero=numero, data=data, fornitore=fornitore or None,
        codice_cliente=codice_cliente, codice_commessa=codice_commessa,
        importo=importo, descrizione=descrizione or None,
        commessa_id=commessa.id if commessa else None,
        fattura_id=fattura_ref.id if fattura_ref else None,
        stato=stato, note_anomalia=note_anomalia,
        file_path=file_path,
        creato_da=current_user.id,
    )
    db.session.add(ddt)
    db.session.commit()

    if stato == 'abbinato':
        flash(f'DDT "{numero}" abbinato correttamente a {commessa.cliente.nome} / {commessa.nome}.', 'success')
    elif stato == 'importo_anomalo':
        flash(f'DDT "{numero}" caricato ma con IMPORTO ANOMALO: {note_anomalia}', 'warning')
    else:
        flash(f'DDT "{numero}" caricato ma NON MATCHATO: {note_anomalia}', 'danger')

    return redirect(url_for('ddt_lista'))


def _cascade_elimina_ddt(ddt):
    """Elimina DDT e tutti i record collegati (RigaDDT, AbbinatoDdtFattura).
    Reimposta stato fattura abbinata se necessario. NON fa commit."""
    # 1. Elimina abbinamenti DDT ↔ Fattura e reimposta stato fattura
    for abb in AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id).all():
        fattura = db.session.get(Fattura, abb.fattura_id)
        if fattura:
            fattura.stato = 'caricata'   # torna a stato pre-abbinamento
        db.session.delete(abb)
    # 2. Elimina righe DDT
    for riga in RigaDDT.query.filter_by(ddt_id=ddt.id).all():
        db.session.delete(riga)
    # 3. Elimina il DDT stesso
    db.session.delete(ddt)


@app.route('/amministrazione/ddt/<int:ddt_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def elimina_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
    else:
        _cascade_elimina_ddt(ddt)
        db.session.commit()
        flash('DDT eliminato con tutti i dati collegati.', 'success')
    return redirect(request.referrer or url_for('ddt_lista'))


# ════════════════════════════════════════════════════════════════════
#  DDT – Revisione Gmail
# ════════════════════════════════════════════════════════════════════

@app.route('/amministrazione/ddt/revisione')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_revisione_lista():
    in_revisione = DDT.query.filter_by(stato='in_revisione').order_by(DDT.creato_il.desc()).all()
    return render_template('ddt_revisione.html',
                           in_revisione=in_revisione,
                           lista_url=url_for('ddt_lista'), **ctx())


@app.route('/amministrazione/ddt/<int:ddt_id>/revisiona')
@login_required
@ruolo_richiesto('amministrazione')
def revisiona_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt or ddt.stato != 'in_revisione':
        flash('DDT non trovato o già processato.', 'danger')
        return redirect(url_for('ddt_revisione_lista'))
    clienti  = Cliente.query.order_by(Cliente.numero).all()
    commesse = Commessa.query.order_by(Commessa.numero).all()
    tecnici  = User.query.filter(
        User.ruolo.in_(['Tecnico', 'Direzione']),
        User.attivo == True
    ).order_by(User.nome).all()

    # ── Suggerisci commessa: 3 livelli di priorità ───────────────────
    commessa_suggerita  = None
    tecnico_suggerito   = None
    metodo_suggerimento = ''
    score_indirizzo     = 0

    # 1) MASSIMA PRIORITÀ: commessa già identificata al momento dell'import
    #    (trovata tramite codice completo C001-3 nel PDF)
    if ddt.commessa_id:
        commessa_suggerita = db.session.get(Commessa, ddt.commessa_id)
        if commessa_suggerita:
            metodo_suggerimento = 'codice completo trovato nel PDF'

    # 2) Codici separati (codice_cliente + codice_commessa estratti)
    if not commessa_suggerita and ddt.codice_cliente and ddt.codice_commessa:
        raw_cl = (ddt.codice_cliente or '').strip().upper().lstrip('C').lstrip('0') or '0'
        try:
            num_cl = int(raw_cl)
            cli = Cliente.query.filter_by(numero=num_cl).first()
            if cli:
                num_com = int((ddt.codice_commessa or '').strip())
                commessa_suggerita = Commessa.query.filter_by(
                    cliente_id=cli.id, numero=num_com
                ).first()
                if commessa_suggerita:
                    metodo_suggerimento = 'codici estratti dal PDF'
        except (ValueError, TypeError):
            pass

    # 3) Fallback: indirizzo cantiere (meno preciso)
    if not commessa_suggerita and ddt.indirizzo_cantiere:
        commessa_suggerita, score_indirizzo = _commessa_per_indirizzo(ddt.indirizzo_cantiere)
        if commessa_suggerita:
            metodo_suggerimento = f'indirizzo cantiere ({score_indirizzo} corrispondenze)'

    if commessa_suggerita:
        tecnico_suggerito = _tecnico_per_commessa(commessa_suggerita)

    # Valori per pre-selezionare i dropdown nel form
    sel_cliente_num  = str(commessa_suggerita.cliente.numero) \
        if commessa_suggerita and commessa_suggerita.cliente else (ddt.codice_cliente or '')
    sel_commessa_num = str(commessa_suggerita.numero) \
        if commessa_suggerita else (ddt.codice_commessa or '')

    # Cerca Fornitore per P.IVA del DDT
    fornitore_suggerito = None
    if ddt.partita_iva:
        piva_n = ddt.partita_iva.strip().upper().lstrip('IT').lstrip('0')
        for forn in Fornitore.query.all():
            if forn.partita_iva and forn.partita_iva.strip().upper().lstrip('IT').lstrip('0') == piva_n:
                fornitore_suggerito = forn
                break

    # Righe già salvate per questo DDT (se presenti dalla revisione precedente)
    righe_salvate = RigaDDT.query.filter_by(ddt_id=ddt.id).all()

    # Righe estratte dal PDF (solo se non già salvate)
    righe_pdf = []
    if not righe_salvate and ddt.file_path and PDF_SUPPORT:
        fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
        if os.path.exists(fp):
            with open(fp, 'rb') as _f:
                pdf_bytes = _f.read()
            righe_pdf = estrai_righe_fattura(pdf_bytes)

    # Tutte le commesse per il dropdown riga-per-riga
    tutte_commesse = Commessa.query.order_by(Commessa.numero).all()

    return render_template('ddt_revisiona.html', ddt=ddt,
                           clienti=clienti, commesse=commesse, tecnici=tecnici,
                           commessa_suggerita=commessa_suggerita,
                           metodo_suggerimento=metodo_suggerimento,
                           tecnico_suggerito=tecnico_suggerito,
                           fornitore_suggerito=fornitore_suggerito,
                           score_indirizzo=score_indirizzo,
                           sel_cliente_num=sel_cliente_num,
                           sel_commessa_num=sel_commessa_num,
                           lista_url=url_for('ddt_revisione_lista'),
                           righe_pdf=righe_pdf,
                           righe_salvate=righe_salvate,
                           tutte_commesse=tutte_commesse, **ctx())


@app.route('/amministrazione/ddt/<int:ddt_id>/conferma', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def conferma_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('ddt_revisione_lista'))

    # Aggiorna campi dal form
    ddt.numero          = request.form.get('numero', ddt.numero or '').strip()
    ddt.fornitore       = request.form.get('fornitore', '').strip() or ddt.fornitore
    ddt.codice_cliente  = request.form.get('codice_cliente', '').strip()
    ddt.codice_commessa = request.form.get('codice_commessa', '').strip()
    ddt.descrizione     = request.form.get('descrizione', '').strip() or None
    ddt.partita_iva     = request.form.get('partita_iva', '').strip() or ddt.partita_iva or None
    try:
        importo_str = request.form.get('importo', '').strip()
        if importo_str:
            ddt.importo = float(importo_str.replace(',', '.'))
    except ValueError:
        pass
    data_str = request.form.get('data', '').strip()
    if data_str:
        try:
            ddt.data = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # ── Trova la commessa dai codici del form ────────────────────────
    commessa = None
    raw_cl = (ddt.codice_cliente or '').strip().upper().lstrip('C').lstrip('0') or '0'
    try:
        num_cl = int(raw_cl)
        cli = Cliente.query.filter_by(numero=num_cl).first()
        if cli:
            num_com = int((ddt.codice_commessa or '').strip())
            commessa = Commessa.query.filter_by(cliente_id=cli.id, numero=num_com).first()
    except (ValueError, TypeError):
        pass
    # Fallback: usa commessa già pre-impostata (rilevata dal PDF all'import)
    if not commessa and ddt.commessa_id:
        commessa = db.session.get(Commessa, ddt.commessa_id)
    if commessa:
        ddt.commessa_id = commessa.id

    # ── Tecnico: manuale dal form, poi automatico dal responsabile commessa ──
    # Priorità 1: scelta esplicita dell'amministrativo dal form
    tecnico = None
    tecnico_id_str = request.form.get('tecnico_id', '').strip()
    if tecnico_id_str:
        try:
            tecnico = db.session.get(User, int(tecnico_id_str))
        except (ValueError, TypeError):
            pass

    assegnazione_auto = False
    # Priorità 2: responsabile della commessa (auto-assegnazione)
    if not tecnico and commessa:
        tecnico = _tecnico_per_commessa(commessa)
        if tecnico:
            assegnazione_auto = True

    # ── Salva righe DDT dal form ─────────────────────────────────────
    # Elimina righe precedenti e risalva quelle nuove
    RigaDDT.query.filter_by(ddt_id=ddt.id).delete()
    descs   = request.form.getlist('riga_desc[]')
    qtas    = request.form.getlist('riga_qta[]')
    pus     = request.form.getlist('riga_pu[]')
    umis    = request.form.getlist('riga_um[]')
    com_ids = request.form.getlist('riga_commessa_id[]')
    for idx, desc in enumerate(descs):
        desc = desc.strip()
        if not desc:
            continue
        def _f(lst, i):
            try:
                v = lst[i].strip().replace(',', '.') if i < len(lst) else ''
                return float(v) if v else None
            except ValueError:
                return None
        qta = _f(qtas, idx)
        pu  = _f(pus,  idx)
        imp = round(qta * pu, 2) if (qta is not None and pu is not None) else None
        cid_str = com_ids[idx].strip() if idx < len(com_ids) else ''
        commessa_riga = None
        if cid_str:
            try:
                commessa_riga = db.session.get(Commessa, int(cid_str))
            except (ValueError, TypeError):
                pass
        um = umis[idx].strip() if idx < len(umis) else ''
        db.session.add(RigaDDT(
            ddt_id=ddt.id,
            commessa_id=commessa_riga.id if commessa_riga else None,
            codice_commessa=commessa_riga.codice if commessa_riga else cid_str or None,
            descrizione=desc[:300],
            quantita=qta, unita_misura=um or None,
            prezzo_unitario=pu, importo=imp
        ))
    db.session.flush()

    if tecnico:
        ddt.tecnico_id = tecnico.id
        ddt.stato = 'in_attesa_tecnico'
        ddt.note_anomalia = None
        db.session.commit()
        if assegnazione_auto:
            flash(
                f'Bolla "{ddt.numero}" assegnata automaticamente a {tecnico.nome} '
                f'(responsabile commessa {commessa.codice}) — inviata per approvazione.',
                'info'
            )
        else:
            flash(
                f'Bolla "{ddt.numero}" inviata a {tecnico.nome} per approvazione'
                f'{(" — Commessa: " + commessa.codice) if commessa else ""}.',
                'info'
            )
    else:
        # Nessun tecnico trovato né in automatico né manualmente
        ddt.stato = 'in_revisione'
        if commessa:
            ddt.note_anomalia = (
                f'Commessa {commessa.codice} trovata ma nessun responsabile assegnato. '
                'Seleziona un tecnico manualmente.'
            )
            flash(
                f'Commessa {commessa.codice} trovata ma non ha un responsabile tecnico. '
                'Assegna un tecnico manualmente dalla revisione.',
                'warning'
            )
        else:
            ddt.note_anomalia = 'Nessuna commessa trovata — assegnare commessa e tecnico manualmente.'
            flash(
                f'Bolla "{ddt.numero}": nessuna commessa identificata. '
                'Seleziona commessa e tecnico manualmente dalla revisione.',
                'warning'
            )
        db.session.commit()

    # ── Auto-match DDT → Fattura passiva via P.IVA + commessa ──────────
    try:
        _rec, _msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
        if _rec:
            db.session.commit()
            flash(f'Abbinamento automatico riuscito: {_msg}', 'success')
    except Exception:
        pass

    n_rimasti = DDT.query.filter_by(stato='in_revisione').count()
    if n_rimasti > 0:
        return redirect(url_for('ddt_revisione_lista'))
    flash('Tutte le bolle in revisione sono state processate.', 'success')
    return redirect(url_for('ddt_lista'))


@app.route('/amministrazione/ddt/<int:ddt_id>/rifiuta', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def rifiuta_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('ddt_revisione_lista'))
    elimina_file(ddt.file_path)
    _cascade_elimina_ddt(ddt)
    db.session.commit()
    flash('DDT rifiutato ed eliminato.', 'info')
    return redirect(url_for('ddt_revisione_lista'))


# ════════════════════════════════════════════════════════════════════
#  DDT – Approvazione Tecnico
# ════════════════════════════════════════════════════════════════════

@app.route('/tecnico/ddt/da-approvare')
@login_required
@ruolo_richiesto('tecnico')
def ddt_da_approvare():
    if current_user.ruolo == 'Direzione':
        # Direzione vede TUTTE le bolle in attesa di qualsiasi tecnico
        bolle = DDT.query.filter_by(
            stato='in_attesa_tecnico'
        ).order_by(DDT.creato_il.desc()).all()
    else:
        # Tecnico vede solo le bolle assegnate a sé
        bolle = DDT.query.filter_by(
            stato='in_attesa_tecnico', tecnico_id=current_user.id
        ).order_by(DDT.creato_il.desc()).all()
    return render_template('ddt_da_approvare.html', bolle=bolle, **ctx())


@app.route('/tecnico/ddt/<int:ddt_id>/approva-bolla', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def approva_bolla(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('Bolla non trovata.', 'danger')
        return redirect(url_for('ddt_da_approvare'))
    # Direzione può approvare qualsiasi bolla; Tecnico solo le proprie
    if current_user.ruolo != 'Direzione' and ddt.tecnico_id != current_user.id:
        flash('Bolla non di tua competenza.', 'danger')
        return redirect(url_for('ddt_da_approvare'))
    ddt.stato = 'approvato_tecnico'
    ddt.motivo_rifiuto = None
    db.session.commit()
    flash(
        f'Bolla "{ddt.numero}" approvata. '
        f"L'amministrazione può ora abbinarla a una fattura.",
        'success'
    )
    # Redirige alla commessa se la chiamata arriva da lì
    next_url = request.form.get('next') or request.referrer or url_for('ddt_da_approvare')
    return redirect(next_url)


@app.route('/tecnico/ddt/<int:ddt_id>/rifiuta-bolla', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def rifiuta_bolla(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('Bolla non trovata.', 'danger')
        return redirect(url_for('ddt_da_approvare'))
    if current_user.ruolo != 'Direzione' and ddt.tecnico_id != current_user.id:
        flash('Bolla non di tua competenza.', 'danger')
        return redirect(url_for('ddt_da_approvare'))
    ddt.stato = 'rifiutato_tecnico'
    ddt.motivo_rifiuto = request.form.get('motivo', '').strip() or None
    db.session.commit()
    flash(
        f'Bolla "{ddt.numero}" rifiutata. '
        f"L'amministrazione ne verrà informata nella sezione Bolle Rifiutate.",
        'info'
    )
    next_url = request.form.get('next') or request.referrer or url_for('ddt_da_approvare')
    return redirect(next_url)


@app.route('/amministrazione/ddt/rifiutate')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_rifiutate():
    rifiutate = DDT.query.filter_by(stato='rifiutato_tecnico').order_by(DDT.creato_il.desc()).all()
    ctx_tecnici = User.query.filter(
        User.ruolo.in_(['Tecnico', 'Direzione']), User.attivo == True
    ).order_by(User.nome).all()
    return render_template('ddt_rifiutate.html', rifiutate=rifiutate,
                           ctx_tecnici=ctx_tecnici,
                           lista_url=url_for('ddt_lista'), **ctx())


# ════════════════════════════════════════════════════════════════════
#  Fattura – Abbinamento DDT
# ════════════════════════════════════════════════════════════════════

@app.route('/amministrazione/fattura/<int:fattura_id>/abbina-ddt', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('amministrazione')
def abbina_ddt_fattura(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('amministrazione'))

    if request.method == 'POST':
        ddt_ids = request.form.getlist('ddt_ids')
        if not ddt_ids:
            flash('Seleziona almeno una bolla da abbinare.', 'warning')
            return redirect(url_for('abbina_ddt_fattura', fattura_id=fattura_id))
        tecnico_assegnato = None
        for sid in ddt_ids:
            try:
                d = db.session.get(DDT, int(sid))
                if d:
                    d.fattura_id = fattura.id
                    d.stato = 'abbinato'
                    if d.tecnico_id and not tecnico_assegnato:
                        tecnico_assegnato = db.session.get(User, d.tecnico_id)
            except (ValueError, TypeError):
                pass
        if tecnico_assegnato:
            fattura.tecnico_id = tecnico_assegnato.id
        fattura.stato_approvazione = 'pending'
        db.session.commit()
        flash(
            f'Bolle abbinate alla fattura {fattura.numero or ""}. '
            f'{"Inviata a " + tecnico_assegnato.nome + " per approvazione." if tecnico_assegnato else "Fattura inviata per approvazione."}',
            'info'
        )
        return redirect(url_for('amministrazione'))

    # GET
    ddt_disponibili = []
    if fattura.commessa_id:
        ddt_disponibili = DDT.query.filter(
            DDT.commessa_id == fattura.commessa_id,
            DDT.stato.in_(['approvato_tecnico', 'non_matchato', 'importo_anomalo'])
        ).order_by(DDT.data.desc()).all()
    ddt_abbinati = DDT.query.filter_by(fattura_id=fattura_id).all()
    return render_template('fattura_abbina_ddt.html',
                           fattura=fattura,
                           ddt_disponibili=ddt_disponibili,
                           ddt_abbinati=ddt_abbinati, **ctx())


# ════════════════════════════════════════════════════════════════════
#  Archivi Fatture + DDT
# ════════════════════════════════════════════════════════════════════

@app.route('/amministrazione/archivi')
@login_required
@ruolo_richiesto('amministrazione')
def archivi():
    fatture_approvate = (
        Fattura.query
        .filter_by(tipo='passiva', stato_approvazione='approvato')
        .filter(Fattura.commessa_id.isnot(None))
        .order_by(Fattura.data_emissione.desc())
        .all()
    )
    commesse_arch = {}
    for f in fatture_approvate:
        linked_ddt = DDT.query.filter_by(fattura_id=f.id).all()
        cid = f.commessa_id
        if cid not in commesse_arch:
            commesse_arch[cid] = {'commessa': f.commessa_obj, 'fatture': []}
        commesse_arch[cid]['fatture'].append({'fattura': f, 'bolle': linked_ddt})
    return render_template('archivi.html',
                           commesse_arch=list(commesse_arch.values()),
                           fatture_con_ddt=[], **ctx())


@app.route('/amministrazione/archivi/commessa/<int:commessa_id>/scarica')
@login_required
@ruolo_richiesto('amministrazione')
def scarica_archivio_commessa(commessa_id):
    import zipfile as zf_mod
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('archivi'))
    buf = io.BytesIO()
    with zf_mod.ZipFile(buf, 'w', zf_mod.ZIP_DEFLATED) as z:
        fatture = Fattura.query.filter_by(
            commessa_id=commessa_id, tipo='passiva', stato_approvazione='approvato'
        ).all()
        for fat in fatture:
            if fat.file_path:
                fp = os.path.join(UPLOAD_ROOT, fat.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'fatture/{os.path.basename(fp)}')
            for ddt in DDT.query.filter_by(fattura_id=fat.id).all():
                tecnico_obj = db.session.get(User, ddt.tecnico_id) if ddt.tecnico_id else None
                sub = f'DDT/{tecnico_obj.nome}/' if tecnico_obj else 'DDT/'
                if ddt.file_path:
                    fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'{sub}{os.path.basename(fp)}')
    buf.seek(0)
    nome = f'archivio_{commessa.codice}_{commessa.nome[:20]}.zip'.replace(' ', '_')
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=nome)


@app.route('/amministrazione/archivi/generale')
@login_required
@ruolo_richiesto('amministrazione')
def scarica_archivio_generale():
    import zipfile as zf_mod
    buf = io.BytesIO()
    with zf_mod.ZipFile(buf, 'w', zf_mod.ZIP_DEFLATED) as z:
        for fat in Fattura.query.filter_by(tipo='passiva', stato_approvazione='approvato').all():
            commessa = db.session.get(Commessa, fat.commessa_id) if fat.commessa_id else None
            folder = commessa.codice.replace('/', '-') if commessa else 'senza_commessa'
            if fat.file_path:
                fp = os.path.join(UPLOAD_ROOT, fat.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'{folder}/fatture/{os.path.basename(fp)}')
            for ddt in DDT.query.filter_by(fattura_id=fat.id).all():
                tecnico_obj = db.session.get(User, ddt.tecnico_id) if ddt.tecnico_id else None
                sub = f'{folder}/DDT/{tecnico_obj.nome}/' if tecnico_obj else f'{folder}/DDT/'
                if ddt.file_path:
                    fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'{sub}{os.path.basename(fp)}')
    buf.seek(0)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True,
                     download_name='archivio_generale_fatture_ddt.zip')



# ════════════════════════════════════════════════════════════════════
#  Download file caricati (alias per i template abbinamenti)
# ════════════════════════════════════════════════════════════════════

@app.route('/download/<path:filename>')
@login_required
def download_upload(filename):
    directory = os.path.join(UPLOAD_ROOT, os.path.dirname(filename))
    fn = os.path.basename(filename)
    return send_from_directory(directory, fn, as_attachment=True)


# ════════════════════════════════════════════════════════════════════
#  Fornitori
# ════════════════════════════════════════════════════════════════════

@app.route('/fornitori')
@login_required
@ruolo_richiesto('fornitori')
def fornitori_lista():
    fornitori = Fornitore.query.order_by(Fornitore.nome).all()
    return render_template('fornitori.html', fornitori=fornitori, **ctx())


@app.route('/fornitori/nuovo', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_nuovo():
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        piva     = request.form.get('partita_iva', '').strip() or None
        cf       = request.form.get('codice_fiscale', '').strip() or None
        indirizzo = request.form.get('indirizzo', '').strip() or None
        email    = request.form.get('email', '').strip() or None
        telefono = request.form.get('telefono', '').strip() or None
        note     = request.form.get('note', '').strip() or None
        if not nome:
            flash('Il nome del fornitore è obbligatorio.', 'warning')
            return redirect(url_for('fornitore_nuovo'))
        if piva:
            exist = Fornitore.query.filter_by(partita_iva=piva).first()
            if exist:
                flash(f'Esiste già un fornitore con P.IVA {piva}: {exist.nome}.', 'warning')
                return redirect(url_for('fornitore_nuovo'))
        f = Fornitore(
            nome=nome, partita_iva=piva, codice_fiscale=cf,
            indirizzo=indirizzo, email=email, telefono=telefono,
            note=note, creato_da=current_user.id
        )
        db.session.add(f)
        db.session.commit()
        flash(f'Fornitore "{nome}" aggiunto.', 'success')
        return redirect(url_for('fornitori_lista'))
    return render_template('fornitore_form.html', fornitore=None, **ctx())


@app.route('/fornitori/<int:fid>/modifica', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_modifica(fid):
    fornitore = db.session.get(Fornitore, fid)
    if not fornitore:
        flash('Fornitore non trovato.', 'danger')
        return redirect(url_for('fornitori_lista'))
    if request.method == 'POST':
        fornitore.nome          = request.form.get('nome', '').strip()
        fornitore.partita_iva   = request.form.get('partita_iva', '').strip() or None
        fornitore.codice_fiscale = request.form.get('codice_fiscale', '').strip() or None
        fornitore.indirizzo     = request.form.get('indirizzo', '').strip() or None
        fornitore.email         = request.form.get('email', '').strip() or None
        fornitore.telefono      = request.form.get('telefono', '').strip() or None
        fornitore.note          = request.form.get('note', '').strip() or None
        if not fornitore.nome:
            flash('Il nome è obbligatorio.', 'warning')
            return redirect(url_for('fornitore_modifica', fid=fid))
        db.session.commit()
        flash(f'Fornitore "{fornitore.nome}" aggiornato.', 'success')
        return redirect(url_for('fornitori_lista'))
    return render_template('fornitore_form.html', fornitore=fornitore, **ctx())


@app.route('/fornitori/<int:fid>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_elimina(fid):
    fornitore = db.session.get(Fornitore, fid)
    if not fornitore:
        flash('Fornitore non trovato.', 'danger')
        return redirect(url_for('fornitori_lista'))
    nome = fornitore.nome
    db.session.delete(fornitore)
    db.session.commit()
    flash(f'Fornitore "{nome}" eliminato.', 'info')
    return redirect(url_for('fornitori_lista'))


# ════════════════════════════════════════════════════════════════════
#  Archivio Abbinamenti DDT ↔ Fatture
# ════════════════════════════════════════════════════════════════════

@app.route('/amministrazione/abbinamenti/ddt-fatture')
@login_required
@ruolo_richiesto('amministrazione')
def archivio_abbinamenti_ddt():
    records = (AbbinatoDdtFattura.query
               .order_by(AbbinatoDdtFattura.abbinato_il.desc())
               .all())
    return render_template('archivio_abbinamenti_ddt.html', records=records, **ctx())


@app.route('/amministrazione/abbinamenti/ddt-fatture/riesegui/<int:ddt_id>', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def riesegui_abbinamento_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('archivio_abbinamenti_ddt'))
    record, msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
    if record:
        db.session.commit()
    flash(msg, 'success' if record else 'warning')
    return redirect(url_for('archivio_abbinamenti_ddt'))


@app.route('/amministrazione/abbinamenti/ddt-fatture/abbina-manuale', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def abbina_ddt_fattura_manuale():
    """Abbinamento manuale DDT ↔ Fattura scelto dall'amministrativo."""
    ddt_id     = request.form.get('ddt_id', '').strip()
    fattura_id = request.form.get('fattura_id', '').strip()
    if not ddt_id or not fattura_id:
        flash('Seleziona sia il DDT che la fattura.', 'warning')
        return redirect(url_for('archivio_abbinamenti_ddt'))
    try:
        ddt     = db.session.get(DDT, int(ddt_id))
        fattura = db.session.get(Fattura, int(fattura_id))
    except (ValueError, TypeError):
        ddt = fattura = None
    if not ddt or not fattura:
        flash('DDT o Fattura non trovati.', 'danger')
        return redirect(url_for('archivio_abbinamenti_ddt'))
    exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=fattura.id).first()
    if exist:
        flash('Abbinamento già presente.', 'info')
        return redirect(url_for('archivio_abbinamenti_ddt'))
    archivio_zip = None
    try:
        import zipfile as _zf
        commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
        dir_zip  = os.path.join(UPLOAD_ROOT, 'archivi', 'ddt_fatture')
        os.makedirs(dir_zip, exist_ok=True)
        nome_zip = (f'DDT_{ddt.numero or ddt.id}_FT_{fattura.numero or fattura.id}'
                    f'_{commessa.codice if commessa else "X"}_MAN.zip'
                    ).replace(' ', '_').replace('/', '-')
        with _zf.ZipFile(os.path.join(dir_zip, nome_zip), 'w', _zf.ZIP_DEFLATED) as z:
            for src, pref in [(ddt.file_path, 'BOLLA'), (fattura.file_path, 'FATTURA')]:
                if src:
                    fp = os.path.join(UPLOAD_ROOT, src)
                    if os.path.exists(fp):
                        z.write(fp, f'{pref}_{os.path.basename(fp)}')
        archivio_zip = os.path.join('archivi', cod_m, nome_zip)
    except Exception:
        pass
    fornitore_man, _ = _fornitore_da_ddt(ddt)
    record = AbbinatoDdtFattura(
        ddt_id=ddt.id, fattura_id=fattura.id,
        commessa_id=ddt.commessa_id,
        fornitore_id=fornitore_man.id if fornitore_man else None,
        score_desc=1.0, archivio_zip=archivio_zip,
        abbinato_da=current_user.id,
    )
    db.session.add(record)
    ddt.fattura_id = fattura.id
    db.session.commit()
    flash(f'DDT {ddt.numero} abbinato manualmente a Fattura {fattura.numero or fattura.id}.', 'success')
    return redirect(url_for('archivio_abbinamenti_ddt'))


# ════════════════════════════════════════════════════════════════════
#  Archivio Abbinamenti Ordini ↔ Subappalti
# ════════════════════════════════════════════════════════════════════

@app.route('/amministrazione/abbinamenti/ordini-subappalti')
@login_required
@ruolo_richiesto('amministrazione')
def archivio_abbinamenti_subappalti():
    records = (AbbinatoOrdineSubappalto.query
               .order_by(AbbinatoOrdineSubappalto.abbinato_il.desc())
               .all())
    return render_template('archivio_abbinamenti_subappalti.html', records=records, **ctx())


# ════════════════════════════════════════════════════════════════════
#  Tesoreria
# ════════════════════════════════════════════════════════════════════

@app.route('/tesoreria')
@login_required
@ruolo_richiesto('tesoreria')
def tesoreria():
    """Schermata Tesoreria — in costruzione."""
    return render_template('tesoreria.html', **ctx())


@app.route('/match-archivi')
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi():
    """Vista match e archivi: bolle ↔ fatture passive per P.IVA fornitore + commessa."""
    oggi = date.today()
    # Tutti i fornitori
    fornitori = Fornitore.query.order_by(Fornitore.nome).all()
    # Abbinamenti DDT ↔ Fatture esistenti
    abbinamenti_ddt = (AbbinatoDdtFattura.query
                       .order_by(AbbinatoDdtFattura.abbinato_il.desc()).all())
    # Abbinamenti Ordini ↔ Subappalti
    abbinamenti_sub = (AbbinatoOrdineSubappalto.query
                       .order_by(AbbinatoOrdineSubappalto.abbinato_il.desc()).all())
    # DDT senza match per P.IVA
    ddt_non_abbinati = DDT.query.filter(
        DDT.stato.notin_(['abbinato']),
        DDT.commessa_id != None
    ).order_by(DDT.creato_il.desc()).all()
    # Fatture passive materiali senza abbinamento DDT
    fat_mat_no_ddt = []
    for f in Fattura.query.filter_by(tipo='passiva', sottotipo='materiali').all():
        if not AbbinatoDdtFattura.query.filter_by(fattura_id=f.id).first():
            fat_mat_no_ddt.append(f)
    # Fatture passive subappalto senza abbinamento ordine
    fat_sub_no_ord = []
    for f in Fattura.query.filter_by(tipo='passiva', sottotipo='subappalto').all():
        if not AbbinatoOrdineSubappalto.query.filter_by(fattura_id=f.id).first():
            fat_sub_no_ord.append(f)
    return render_template('match_archivi.html',
                           fornitori=fornitori,
                           abbinamenti_ddt=abbinamenti_ddt,
                           abbinamenti_sub=abbinamenti_sub,
                           ddt_non_abbinati=ddt_non_abbinati,
                           fat_mat_no_ddt=fat_mat_no_ddt,
                           fat_sub_no_ord=fat_sub_no_ord,
                           oggi=oggi, **ctx())


@app.route('/match-archivi/match-ddt-manuale', methods=['POST'])
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi_ddt():
    """Abbina manualmente un DDT a una fattura passiva materiali dalla tesoreria."""
    ddt_id     = request.form.get('ddt_id', '').strip()
    fattura_id = request.form.get('fattura_id', '').strip()
    if not ddt_id or not fattura_id:
        flash('Seleziona DDT e Fattura.', 'warning')
        return redirect(url_for('match_archivi'))
    try:
        ddt     = db.session.get(DDT, int(ddt_id))
        fattura = db.session.get(Fattura, int(fattura_id))
    except (ValueError, TypeError):
        ddt = fattura = None
    if not ddt or not fattura:
        flash('DDT o Fattura non trovati.', 'danger')
        return redirect(url_for('match_archivi'))
    record, msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
    if record:
        db.session.commit()
        flash(f'Match completato: {msg}', 'success')
    else:
        # Tenta abbinamento manuale forzato
        exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=fattura.id).first()
        if not exist:
            archivio_zip = None
            try:
                import zipfile as _zf
                commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
                cod_comm = (commessa.codice if commessa else 'SENZA_COMMESSA').replace(' ', '_').replace('/', '-')
                dir_zip = os.path.join(UPLOAD_ROOT, 'archivi', cod_comm)
                os.makedirs(dir_zip, exist_ok=True)
                dir_gen  = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti')
                os.makedirs(dir_gen, exist_ok=True)
                nome_zip = (f'MAN_DDT_{ddt.numero or ddt.id}_FT_{fattura.numero or fattura.id}'
                            f'_{cod_comm}.zip').replace(' ', '_').replace('/', '-')
                zip_path = os.path.join(dir_zip, nome_zip)
                with _zf.ZipFile(zip_path, 'w', _zf.ZIP_DEFLATED) as z:
                    for src_path, pref in [(ddt.file_path, 'BOLLA'), (fattura.file_path, 'FATTURA')]:
                        if src_path:
                            fp = os.path.join(UPLOAD_ROOT, src_path)
                            if os.path.exists(fp):
                                z.write(fp, f'{pref}_{os.path.basename(fp)}')
                import shutil as _shutil2
                _shutil2.copy2(zip_path, os.path.join(dir_gen, nome_zip))
                archivio_zip = os.path.join('archivi', cod_comm, nome_zip)
            except Exception:
                pass
            fornitore_match2, _ = _fornitore_da_ddt(ddt)
            record = AbbinatoDdtFattura(
                ddt_id=ddt.id, fattura_id=fattura.id,
                commessa_id=ddt.commessa_id,
                fornitore_id=fornitore_match2.id if fornitore_match2 else None,
                score_desc=1.0, archivio_zip=archivio_zip,
                abbinato_da=current_user.id,
            )
            db.session.add(record)
            ddt.fattura_id = fattura.id
            ddt.stato = 'abbinato'
            db.session.commit()
            flash(f'Match manuale creato: DDT {ddt.numero} ↔ Fattura {fattura.numero or fattura.id}', 'success')
        else:
            flash('Abbinamento già presente.', 'info')
    return redirect(url_for('match_archivi'))


@app.route('/match-archivi/scarica-tutti')
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi_scarica_tutti():
    """Genera un unico ZIP con tutti gli archivi DDT↔Fattura abbinati."""
    import zipfile as _zf2
    records = AbbinatoDdtFattura.query.filter(
        AbbinatoDdtFattura.archivio_zip != None
    ).all()
    # Aggiungi anche abbinamenti Ordini↔Subappalti
    records_sub = AbbinatoOrdineSubappalto.query.filter(
        AbbinatoOrdineSubappalto.archivio_zip != None
    ).all()
    buf = io.BytesIO()
    added = 0
    with _zf2.ZipFile(buf, 'w', _zf2.ZIP_DEFLATED) as z:
        for ab in records:
            if ab.archivio_zip:
                fp = os.path.join(UPLOAD_ROOT, ab.archivio_zip)
                if os.path.exists(fp):
                    comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                    arcname = f'DDT_Fatture/{comm}/{os.path.basename(fp)}'
                    z.write(fp, arcname); added += 1
                else:
                    # Prova nella cartella 'tutti'
                    fp2 = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti', os.path.basename(ab.archivio_zip))
                    if os.path.exists(fp2):
                        comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                        z.write(fp2, f'DDT_Fatture/{comm}/{os.path.basename(fp2)}'); added += 1
        for ab in records_sub:
            if ab.archivio_zip:
                fp = os.path.join(UPLOAD_ROOT, ab.archivio_zip)
                if os.path.exists(fp):
                    comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                    z.write(fp, f'Ordini_Subappalti/{comm}/{os.path.basename(fp)}'); added += 1
    if added == 0:
        flash('Nessun archivio ZIP disponibile da scaricare.', 'warning')
        return redirect(url_for('match_archivi'))
    buf.seek(0)
    from datetime import date as _date
    nome = f'Archivio_Completo_Abbinamenti_{_date.today().strftime("%Y%m%d")}.zip'
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=nome)


# ════════════════════════════════════════════════════════════════════
#  Fattura Attiva
# ════════════════════════════════════════════════════════════════════

@app.route('/tecnico/commessa/<int:commessa_id>/crea-fattura-attiva', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def crea_fattura_attiva_commessa(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa or not _check_cliente_access(commessa.cliente):
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico'))
    def _pd(s):
        try: return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        except ValueError: return None
    def _fv(v):
        try: return float(str(v).replace(',', '.')) if v else None
        except ValueError: return None
    numero = request.form.get('numero', '').strip()
    if not numero:
        contatore = Fattura.query.filter_by(tipo='attiva', commessa_id=commessa.id).count() + 1
        numero = f'FT-{commessa.codice}-{date.today().strftime("%Y")}-{contatore:03d}'
    aliquota_iva = _fv(request.form.get('aliquota_iva', '22'))
    importo_netto = _fv(request.form.get('importo_netto', '0'))
    if importo_netto and aliquota_iva:
        importo = round(importo_netto * (1 + aliquota_iva / 100), 2)
    else:
        importo = _fv(request.form.get('importo', '0')) or 0.0
    fattura = Fattura(
        tipo='attiva', numero=numero, importo=importo, importo_netto=importo_netto,
        aliquota_iva=aliquota_iva, stato_approvazione='approvato',
        cliente_fornitore=request.form.get('cliente_fornitore', commessa.cliente.nome if commessa.cliente else '').strip(),
        piva_cliente=request.form.get('piva_cliente', '').strip() or None,
        mia_piva=request.form.get('mia_piva', '').strip() or None,
        indirizzo_cliente=request.form.get('indirizzo_cliente', '').strip() or None,
        pec_cliente=request.form.get('pec_cliente', '').strip() or None,
        cup=request.form.get('cup', '').strip() or None,
        cig=request.form.get('cig', '').strip() or None,
        codice_commessa_ref=commessa.codice,
        data_emissione=_pd(request.form.get('data_emissione', '')) or date.today(),
        data_scadenza=_pd(request.form.get('data_scadenza', '')),
        note=request.form.get('note', '').strip() or None,
        cliente_id=commessa.cliente_id, commessa_id=commessa.id,
    )
    db.session.add(fattura)
    db.session.flush()
    for i2, desc2 in enumerate(request.form.getlist('riga_desc[]')):
        desc2 = desc2.strip()
        if not desc2: continue
        art2  = (request.form.getlist('riga_articolo[]')[i2:i2+1] or [''])[0].strip()
        qta2  = _fv((request.form.getlist('riga_qta[]')[i2:i2+1]  or [''])[0])
        um2   = (request.form.getlist('riga_um[]')[i2:i2+1]       or [''])[0].strip()
        pu2   = _fv((request.form.getlist('riga_pu[]')[i2:i2+1]   or [''])[0])
        imp2  = round(qta2 * pu2, 2) if (qta2 and pu2) else None
        db.session.add(RigaFatturaAttiva(
            fattura_id=fattura.id, articolo=art2 or None,
            descrizione=desc2[:300], quantita=qta2,
            unita_misura=um2 or None, prezzo_unitario=pu2, importo_netto=imp2,
        ))
    db.session.commit()
    flash(f'Fattura attiva {numero} creata (€ {importo:,.2f} IVA inclusa).', 'success')
    return redirect(url_for('commessa_detail', commessa_id=commessa_id, tab='fatture_attive'))



@app.route('/tecnico/commessa/<int:commessa_id>/fattura-attiva/<int:fattura_id>/pdf')
@login_required
def scarica_pdf_fattura_attiva(commessa_id, fattura_id):
    """Genera il PDF della fattura attiva"""
    fattura  = db.session.get(Fattura, fattura_id)
    commessa = db.session.get(Commessa, commessa_id)
    if not fattura or fattura.commessa_id != commessa_id:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('tecnico'))

    import io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, HRFlowable)
    from reportlab.pdfbase import pdfmetrics

    # ── Palette identica all'ordine ───────────────────────────────────────────
    C_DARK   = colors.HexColor('#1e1b4b')
    C_MID    = colors.HexColor('#4338ca')
    C_LIGHT  = colors.HexColor('#e0e7ff')
    C_ACCENT = colors.HexColor('#6366f1')
    C_GREY   = colors.HexColor('#64748b')
    C_ROW    = colors.HexColor('#f5f3ff')
    C_GREEN  = colors.HexColor('#15803d')
    C_WHITE  = colors.white
    C_BLACK  = colors.HexColor('#0f172a')

    def ps(name, fontName='Helvetica-Bold', fontSize=10, textColor=C_BLACK,
           alignment=TA_LEFT, leading=None, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=fontName, fontSize=fontSize,
                              textColor=textColor, alignment=alignment,
                              leading=leading or fontSize * 1.3,
                              spaceBefore=spaceBefore, spaceAfter=spaceAfter)

    sty_brand_big  = ps('BB',  'Helvetica-Bold',  33, C_WHITE, TA_LEFT)
    sty_brand_sub  = ps('BS',  'Helvetica-Bold',   9, colors.HexColor('#fbbf24'), TA_LEFT)
    sty_label      = ps('LB',  'Helvetica',         7, C_GREY,  TA_LEFT)
    sty_value      = ps('VL',  'Helvetica-Bold',    9, C_BLACK, TA_LEFT)
    sty_doc_num    = ps('ON',  'Helvetica-Bold',   13, C_DARK,  TA_LEFT)
    sty_th         = ps('TH',  'Helvetica-Bold',    8, C_WHITE, TA_CENTER)
    sty_td         = ps('TD',  'Helvetica',          8, C_BLACK, TA_LEFT)
    sty_td_r       = ps('TDR', 'Helvetica',          8, C_BLACK, TA_RIGHT)
    sty_td_bold    = ps('TDB', 'Helvetica-Bold',    8, C_BLACK, TA_RIGHT)
    sty_footer     = ps('FT',  'Helvetica',          7, C_GREY,  TA_CENTER)
    sty_note_lbl   = ps('NL',  'Helvetica-Bold',    8, C_GREY,  TA_LEFT)
    sty_note_val   = ps('NV',  'Helvetica',          8, C_BLACK, TA_LEFT)

    buf = io.BytesIO()
    W, H = A4
    margin = 1.8 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin + 0.5*cm)
    story = []

    # ── HEADER — logo ARTEDILE (sinistra) | dati fattura (destra) ────────────
    logo_inner = Table(
        [[Paragraph('<font color="#818cf8">ARTE</font><font color="#ffffff">DILE</font>',
                    sty_brand_big)],
         [Paragraph('&#9656;  Impianti ed Edilizia', sty_brand_sub)]],
        colWidths=[7.5*cm], rowHeights=[1.9*cm, 0.75*cm]
    )
    logo_inner.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), colors.HexColor('#0d1117')),
        ('TOPPADDING',    (0,0), (0,0),   14),
        ('BOTTOMPADDING', (0,1), (0,1),   13),
        ('LEFTPADDING',   (0,0), (-1,-1), 18),
        ('RIGHTPADDING',  (0,0), (-1,-1), 18),
        ('ROUNDEDCORNERS',[10]),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW',     (0,0), (0,0),   0.8, colors.HexColor('#818cf8')),
    ]))

    ft_num_str  = fattura.numero or f'FT-{fattura.id}'
    ft_data_str = fattura.data_emissione.strftime('%d %B %Y').upper() if fattura.data_emissione else ''
    ft_scad_str = fattura.data_scadenza.strftime('%d/%m/%Y') if fattura.data_scadenza else '—'

    logo_col = Table(
        [[logo_inner],
         [Spacer(1, 6)],
         [Paragraph(f'FATTURA  {ft_num_str}', sty_doc_num)],
         [Paragraph(ft_data_str, ps('DT','Helvetica', 8, C_GREY))]],
        colWidths=[7*cm]
    )
    logo_col.setStyle(TableStyle([
        ('LEFTPADDING',   (0,0),(-1,-1), 0),
        ('RIGHTPADDING',  (0,0),(-1,-1), 0),
        ('TOPPADDING',    (0,0),(-1,-1), 0),
        ('BOTTOMPADDING', (0,0),(-1,-1), 2),
    ]))

    def ref_row(label, value):
        return [Paragraph(label, sty_label), Paragraph(str(value or '—'), sty_value)]

    azienda_piva = AppConfig.get('azienda_piva', fattura.mia_piva or '')
    azienda_ind  = AppConfig.get('azienda_indirizzo', '')
    ref_data = [
        ref_row('DESTINATARIO',  fattura.cliente_fornitore),
        ref_row('P.IVA CLIENTE', fattura.piva_cliente),
        ref_row('COMMESSA',      f'{commessa.codice} — {commessa.nome}' if commessa else ''),
        ref_row('SCADENZA',      ft_scad_str),
    ]
    if fattura.cup: ref_data.append(ref_row('CUP', fattura.cup))
    if fattura.cig: ref_data.append(ref_row('CIG', fattura.cig))
    if azienda_piva: ref_data.append(ref_row('P.IVA EMITTENTE', azienda_piva))
    if azienda_ind:  ref_data.append(ref_row('INDIRIZZO',        azienda_ind))

    ref_table = Table(ref_data, colWidths=[2.8*cm, 6.6*cm])
    ref_table.setStyle(TableStyle([
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING',    (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING',   (0,0), (-1,-1), 0),
        ('RIGHTPADDING',  (0,0), (-1,-1), 0),
        ('LINEBELOW',     (0,0), (-1,-2), 0.3, colors.HexColor('#e0e7ff')),
    ]))

    header = Table([[logo_col, ref_table]], colWidths=[8*cm, 9.8*cm])
    header.setStyle(TableStyle([
        ('VALIGN',        (0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',   (0,0),(-1,-1), 0),
        ('RIGHTPADDING',  (0,0),(-1,-1), 0),
        ('TOPPADDING',    (0,0),(-1,-1), 0),
        ('BOTTOMPADDING', (0,0),(-1,-1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1,12))
    story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=12))

    # ── TABELLA RIGHE ─────────────────────────────────────────────────────────
    righe_ft = RigaFatturaAttiva.query.filter_by(fattura_id=fattura.id).all()
    col_w = [1.2*cm, 7.2*cm, 1.4*cm, 1.3*cm, 2.7*cm, 2.7*cm]

    tbl_data = [[
        Paragraph('Art.',         sty_th),
        Paragraph('Descrizione',  sty_th),
        Paragraph('Qtà',          sty_th),
        Paragraph('U.M.',         sty_th),
        Paragraph('Prezzo unit.', sty_th),
        Paragraph('Importo netto',sty_th),
    ]]

    tot_netto = 0.0
    if righe_ft:
        for i, r in enumerate(righe_ft):
            imp = r.importo_netto or ((r.quantita or 0) * (r.prezzo_unitario or 0))
            tot_netto += imp or 0
            tbl_data.append([
                Paragraph(r.articolo or '', sty_td),
                Paragraph(r.descrizione or '', sty_td),
                Paragraph(f'{r.quantita:g}'.replace('.',',') if r.quantita else '', sty_td_r),
                Paragraph(r.unita_misura or '', sty_td),
                Paragraph(f'€ {r.prezzo_unitario:,.2f}' if r.prezzo_unitario else '', sty_td_r),
                Paragraph(f'€ {imp:,.2f}' if imp else '—', sty_td_bold),
            ])
    else:
        tot_netto = fattura.importo_netto or 0
        tbl_data.append([
            Paragraph('', sty_td),
            Paragraph('Servizi / Forniture', sty_td),
            Paragraph('1', sty_td_r),
            Paragraph('', sty_td),
            Paragraph(f'€ {tot_netto:,.2f}', sty_td_r),
            Paragraph(f'€ {tot_netto:,.2f}', sty_td_bold),
        ])

    # Riga totale imponibile
    tbl_data.append([
        Paragraph('', sty_th), Paragraph('', sty_th),
        Paragraph('', sty_th), Paragraph('', sty_th),
        Paragraph('IMPONIBILE', ps('TL','Helvetica-Bold',9,C_DARK,TA_RIGHT)),
        Paragraph(f'€ {tot_netto:,.2f}', ps('TV','Helvetica-Bold',11,C_ACCENT,TA_RIGHT)),
    ])

    n_righe = len(tbl_data) - 2  # righe dati
    voci_table = Table(tbl_data, colWidths=col_w, repeatRows=1)
    row_styles = [
        ('BACKGROUND',    (0,0), (-1,0),  C_DARK),
        ('TEXTCOLOR',     (0,0), (-1,0),  C_WHITE),
        ('ROWBACKGROUND', (0,1), (-1,n_righe), [C_WHITE, C_ROW]),
        ('BACKGROUND',    (0,-1),(-1,-1), C_LIGHT),
        ('LINEABOVE',     (0,-1),(-1,-1), 1.5, C_ACCENT),
        ('TOPPADDING',    (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('LEFTPADDING',   (0,0), (-1,-1), 6),
        ('RIGHTPADDING',  (0,0), (-1,-1), 6),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('INNERGRID',     (0,1), (-1,-2), 0.3, colors.HexColor('#e0e7ff')),
        ('BOX',           (0,0), (-1,-1), 0.5, C_MID),
        ('ROUNDEDCORNERS',[4]),
    ]
    voci_table.setStyle(TableStyle(row_styles))
    story.append(voci_table)
    story.append(Spacer(1,10))

    # ── RIEPILOGO IVA + TOTALE ────────────────────────────────────────────────
    aliq  = fattura.aliquota_iva or 22.0
    iva   = round(tot_netto * aliq / 100, 2)
    total = round(tot_netto + iva, 2)

    summ_data = [
        [Paragraph('Imponibile',      ps('SL','Helvetica',9,C_GREY,TA_RIGHT)),
         Paragraph(f'€ {tot_netto:,.2f}', ps('SV','Helvetica-Bold',9,C_BLACK,TA_RIGHT))],
        [Paragraph(f'IVA {aliq:g}%',  ps('IL','Helvetica',9,C_GREY,TA_RIGHT)),
         Paragraph(f'€ {iva:,.2f}',    ps('IV','Helvetica',9,C_BLACK,TA_RIGHT))],
        [Paragraph('TOTALE FATTURA',  ps('TL','Helvetica-Bold',11,C_GREEN,TA_RIGHT)),
         Paragraph(f'€ {total:,.2f}', ps('TV','Helvetica-Bold',11,C_GREEN,TA_RIGHT))],
    ]
    summ = Table(summ_data, colWidths=[12*cm, 4.5*cm])
    summ.setStyle(TableStyle([
        ('TOPPADDING',    (0,0),(-1,-1), 4),
        ('BOTTOMPADDING', (0,0),(-1,-1), 4),
        ('LEFTPADDING',   (0,0),(-1,-1), 6),
        ('RIGHTPADDING',  (0,0),(-1,-1), 6),
        ('LINEABOVE',     (0,2),(-1,2), 1.5, C_GREEN),
        ('BACKGROUND',    (0,2),(-1,2), colors.HexColor('#f0fdf4')),
    ]))
    story.append(summ)

    # ── NOTE ─────────────────────────────────────────────────────────────────
    if fattura.note:
        story.append(Spacer(1,14))
        note_box = Table(
            [[Paragraph('NOTE', sty_note_lbl)],
             [Paragraph(fattura.note, sty_note_val)]],
            colWidths=[doc.width]
        )
        note_box.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), colors.HexColor('#faf5ff')),
            ('BOX',           (0,0),(-1,-1), 0.5, colors.HexColor('#c7d2fe')),
            ('TOPPADDING',    (0,0),(-1,-1), 8),
            ('BOTTOMPADDING', (0,0),(-1,-1), 8),
            ('LEFTPADDING',   (0,0),(-1,-1), 10),
            ('RIGHTPADDING',  (0,0),(-1,-1), 10),
            ('ROUNDEDCORNERS',[6]),
        ]))
        story.append(note_box)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    story.append(Spacer(1,20))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_LIGHT, spaceAfter=6))
    story.append(Paragraph(
        f'ARTEDILE  ·  Documento generato il {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}  ·  {ft_num_str}',
        sty_footer
    ))

    doc.build(story)
    buf.seek(0)
    nome_pdf = f'Fattura_ARTEDILE_{ft_num_str.replace("/","-")}.pdf'
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=nome_pdf)

@app.route('/tecnico/ordine/<int:ordine_id>/crea-fattura-attiva', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def crea_fattura_da_ordine(ordine_id):
    """Crea una fattura attiva automaticamente partendo dai dati dell'ordine."""
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico'))

    cliente = db.session.get(Cliente, ordine.cliente_id)
    numero_ft = f'FT-{ordine.identificativo or ordine.id}-{datetime.utcnow().strftime("%Y%m%d")}'
    note_ft = (f'Fattura generata da Ordine {ordine.identificativo or ordine.id}. '
               f'{ordine.note or ""}').strip()

    fattura = Fattura(
        tipo='attiva',
        sottotipo=None,
        numero=numero_ft,
        importo=ordine.totale or 0.0,
        cliente_fornitore=cliente.nome if cliente else (ordine.azienda or ''),
        data_emissione=date.today(),
        data_scadenza=None,
        note=note_ft,
        stato_approvazione='approvato',
        cliente_id=ordine.cliente_id,
        commessa_id=ordine.commessa_id,
    )
    db.session.add(fattura)
    db.session.commit()
    flash(
        f'Fattura attiva {numero_ft} creata automaticamente '
        f'(€ {ordine.totale:,.2f}) — verifica e completa in Amministrazione.',
        'success'
    )
    return redirect(url_for('ordine_dettaglio', ordine_id=ordine_id))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))

    def apri_browser():
        webbrowser.open(f'http://localhost:{port}')

    threading.Timer(1.5, apri_browser).start()
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
