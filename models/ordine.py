from datetime import datetime
from extensions import db


class Ordine(db.Model):
    __tablename__ = 'ordini'
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=False)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    identificativo = db.Column(db.String(50))
    azienda = db.Column(db.String(200))
    piva = db.Column(db.String(50))
    rif_ordine = db.Column(db.String(100))
    dati_cantiere = db.Column(db.String(300))
    indirizzo = db.Column(db.String(300))
    rif_cantiere = db.Column(db.String(100))
    note = db.Column(db.Text)
    cig = db.Column(db.String(200))
    cup = db.Column(db.String(200))
    totale = db.Column(db.Float, default=0.0)
    stato = db.Column(db.String(30), default='bozza')
    creato_da = db.Column(db.Integer, db.ForeignKey('users.id'))
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    righe = db.relationship('RigaOrdine', backref='ordine', lazy=True,
                            cascade='all, delete-orphan', order_by='RigaOrdine.numero')


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
