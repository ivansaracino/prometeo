from extensions import db


class AppConfig(db.Model):
    __tablename__ = 'app_config'
    id = db.Column(db.Integer, primary_key=True)
    chiave = db.Column(db.String(100), unique=True, nullable=False)
    valore = db.Column(db.Text)

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
