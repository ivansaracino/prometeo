from datetime import datetime
from extensions import db


class DDT(db.Model):
    __tablename__ = 'ddt'
    id = db.Column(db.Integer, primary_key=True)
    numero = db.Column(db.String(50))
    data = db.Column(db.Date)
    fornitore = db.Column(db.String(200))
    partita_iva = db.Column(db.String(30), nullable=True)
    codice_cliente = db.Column(db.String(20))
    codice_commessa = db.Column(db.String(20))
    importo = db.Column(db.Float, default=0.0)
    descrizione = db.Column(db.Text)
    indirizzo_cantiere = db.Column(db.String(500), nullable=True)
    tecnico_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    motivo_rifiuto = db.Column(db.Text, nullable=True)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    fattura_id = db.Column(db.Integer, db.ForeignKey('fatture.id'), nullable=True)
    stato = db.Column(db.String(30), default='non_matchato')
    note_anomalia = db.Column(db.Text)
    file_path = db.Column(db.String(500), nullable=True)
    creato_il = db.Column(db.DateTime, default=datetime.utcnow)
    creato_da = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    commessa = db.relationship('Commessa', backref='ddt', lazy=True, foreign_keys=[commessa_id])
    fattura = db.relationship('Fattura', backref='ddt', lazy=True, foreign_keys=[fattura_id])
    tecnico = db.relationship('User', backref='ddt_assegnati', lazy=True, foreign_keys=[tecnico_id])


class RigaDDT(db.Model):
    __tablename__ = 'righe_ddt'
    id = db.Column(db.Integer, primary_key=True)
    ddt_id = db.Column(db.Integer, db.ForeignKey('ddt.id'), nullable=False)
    commessa_id = db.Column(db.Integer, db.ForeignKey('commesse.id'), nullable=True)
    codice_commessa = db.Column(db.String(30), nullable=True)
    descrizione = db.Column(db.Text, nullable=False)
    quantita = db.Column(db.Float, nullable=True)
    unita_misura = db.Column(db.String(20), nullable=True)
    prezzo_unitario = db.Column(db.Float, nullable=True)
    importo = db.Column(db.Float, nullable=True)
    ddt = db.relationship('DDT', backref='righe', lazy=True)
    commessa = db.relationship('Commessa', backref='righe_ddt', lazy=True)

    def calc_importo(self):
        if self.quantita and self.prezzo_unitario:
            return round(self.quantita * self.prezzo_unitario, 2)
        return self.importo or 0.0
