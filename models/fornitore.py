from datetime import datetime
from extensions import db


class Fornitore(db.Model):
    __tablename__ = 'fornitori'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200), nullable=False)
    partita_iva = db.Column(db.String(30), nullable=True)
    codice_fiscale = db.Column(db.String(20), nullable=True)
    indirizzo = db.Column(db.String(300), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    telefono = db.Column(db.String(50), nullable=True)
    note = db.Column(db.Text, nullable=True)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    creato_da = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
