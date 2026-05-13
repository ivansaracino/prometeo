from datetime import datetime
from extensions import db


class AbbinatoDdtFattura(db.Model):
    __tablename__ = 'abbinati_ddt_fattura'
    id = db.Column(db.Integer, primary_key=True)
    ddt_id = db.Column(db.Integer, db.ForeignKey('ddt.id'), nullable=False)
    fattura_id = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=False)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    fornitore_id = db.Column(db.Integer, db.ForeignKey('fornitori.id'), nullable=True)
    score_desc = db.Column(db.Float, default=0.0)
    archivio_zip = db.Column(db.String(500), nullable=True)
    abbinato_il = db.Column(db.DateTime, default=datetime.utcnow)
    abbinato_da = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ddt = db.relationship('DDT', backref='abbinamento_ddt_fattura', lazy=True)
    fattura = db.relationship('Fattura', backref='abbinamento_ddt', lazy=True)
    commessa = db.relationship('Commessa', backref='abbinamenti_ddt', lazy=True)
    fornitore = db.relationship('Fornitore', backref='abbinamenti', lazy=True)


class AbbinatoOrdineSubappalto(db.Model):
    __tablename__ = 'abbinati_ordine_subappalto'
    id = db.Column(db.Integer, primary_key=True)
    ordine_id = db.Column(db.Integer, db.ForeignKey('ordini.id'), nullable=False)
    fattura_id = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=False)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    stato = db.Column(db.String(20), default='ok')
    delta_importo = db.Column(db.Float, default=0.0)
    score_desc = db.Column(db.Float, default=0.0)
    archivio_zip = db.Column(db.String(500), nullable=True)
    abbinato_il = db.Column(db.DateTime, default=datetime.utcnow)
    abbinato_da = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    ordine = db.relationship('Ordine', backref='abbinamento_subappalto', lazy=True)
    fattura = db.relationship('Fattura', backref='abbinamento_ordine', lazy=True)
    commessa = db.relationship('Commessa', backref='abbinamenti_subappalto', lazy=True)
