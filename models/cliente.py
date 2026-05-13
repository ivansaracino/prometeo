from datetime import datetime
from extensions import db


class Cliente(db.Model):
    __tablename__ = 'clienti'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.Integer)
    nome = db.Column(db.String(200), nullable=False)
    partita_iva = db.Column(db.String(30))
    cig = db.Column(db.String(100))
    cup = db.Column(db.String(100))
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
        num_cl = (self.cliente.numero if self.cliente and self.cliente.numero else
                  (self.cliente.id if self.cliente else 0))
        return f'C{num_cl:03d}-{self.numero}'


class CostoFornitore(db.Model):
    __tablename__ = 'costi_fornitori'
    id = db.Column(db.Integer, primary_key=True)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    commessa_suggerita_id = db.Column(db.Integer, nullable=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('clienti.id'), nullable=True)
    categoria = db.Column(db.String(50), nullable=False)
    sottocategoria = db.Column(db.String(20))
    descrizione = db.Column(db.String(300), nullable=False)
    quantita = db.Column(db.Float)
    valore_unitario = db.Column(db.Float)
    importo = db.Column(db.Float, nullable=False, default=0.0)
    stato = db.Column(db.String(20), default='approvato')
    data = db.Column(db.Date, default=datetime.utcnow)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
