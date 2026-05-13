from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from config import PERMESSI


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(200))
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
