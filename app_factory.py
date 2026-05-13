import os
from flask import Flask
from extensions import db, login_manager
from config import UPLOAD_ROOT


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-prod')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///webapp.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024

    db.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Devi effettuare il login per accedere a questa pagina.'
    login_manager.login_message_category = 'warning'

    app.jinja_env.globals['chr'] = chr

    from blueprints.auth import bp as auth_bp
    from blueprints.tecnico import bp as tecnico_bp
    from blueprints.amministrazione import bp as amministrazione_bp
    from blueprints.owner import bp as owner_bp
    from blueprints.banca import bp as banca_bp
    from blueprints.risorse_umane import bp as risorse_umane_bp
    from blueprints.fornitori import bp as fornitori_bp
    from blueprints.operaio import bp as operaio_bp
    from blueprints.admin import bp as admin_bp
    from blueprints.match_archivi import bp as match_archivi_bp
    from blueprints.tesoreria import bp as tesoreria_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(tecnico_bp)
    app.register_blueprint(amministrazione_bp)
    app.register_blueprint(owner_bp)
    app.register_blueprint(banca_bp)
    app.register_blueprint(risorse_umane_bp)
    app.register_blueprint(fornitori_bp)
    app.register_blueprint(operaio_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(match_archivi_bp)
    app.register_blueprint(tesoreria_bp)

    with app.app_context():
        db.create_all()

    return app
