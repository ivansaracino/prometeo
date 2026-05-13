from datetime import datetime
from extensions import db


class Fattura(db.Model):
    __tablename__ = 'fatture'
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(10), nullable=False)
    sottotipo = db.Column(db.String(20))
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
    stato_approvazione = db.Column(db.String(20), default='approvato')
    codice_cliente_ref = db.Column(db.String(20))
    codice_commessa_ref = db.Column(db.String(20))
    tecnico_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    motivo_rifiuto = db.Column(db.Text, nullable=True)
    partita_iva_fornitore = db.Column(db.String(30), nullable=True)
    piva_cliente = db.Column(db.String(30), nullable=True)
    mia_piva = db.Column(db.String(30), nullable=True)
    indirizzo_cliente = db.Column(db.String(500), nullable=True)
    pec_cliente = db.Column(db.String(200), nullable=True)
    cup = db.Column(db.String(50), nullable=True)
    cig = db.Column(db.String(50), nullable=True)
    aliquota_iva = db.Column(db.Float, nullable=True)
    importo_netto = db.Column(db.Float, nullable=True)


class RigaFatturaAttiva(db.Model):
    __tablename__ = 'righe_fattura_attiva'
    id = db.Column(db.Integer, primary_key=True)
    fattura_id = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=False)
    articolo = db.Column(db.String(100), nullable=True)
    descrizione = db.Column(db.Text, nullable=False)
    quantita = db.Column(db.Float, nullable=True)
    unita_misura = db.Column(db.String(20), nullable=True)
    prezzo_unitario = db.Column(db.Float, nullable=True)
    importo_netto = db.Column(db.Float, nullable=True)
    fattura = db.relationship('Fattura', backref='righe_attiva', lazy=True)
