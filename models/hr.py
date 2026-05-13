from datetime import datetime
from extensions import db


class Dipendente(db.Model):
    __tablename__ = 'dipendenti'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    cognome = db.Column(db.String(100), nullable=False)
    costo_orario = db.Column(db.Float, default=0.0)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    tutti_documenti = db.relationship('DocumentoDipendente', backref='dipendente',
                                      lazy=True, cascade='all, delete-orphan')


class DocumentoDipendente(db.Model):
    __tablename__ = 'documenti_dipendenti'
    id = db.Column(db.Integer, primary_key=True)
    dipendente_id = db.Column(db.Integer, db.ForeignKey('dipendenti.id'), nullable=False)
    nome = db.Column(db.String(200), nullable=False)
    nome_file = db.Column(db.String(400))
    tipo = db.Column(db.String(20), nullable=False)
    data_scadenza = db.Column(db.Date)
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
