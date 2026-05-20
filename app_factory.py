import os
from flask import Flask
from extensions import db, login_manager
from config import UPLOAD_ROOT

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-prod')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///webapp.db')
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
    from blueprints.mezzi import bp as mezzi_bp

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
    app.register_blueprint(mezzi_bp)

    with app.app_context():
        db.create_all()
        _migrate(db)

    # ── Context processor globale: rende disponibile in OGNI template
    #    `nome_societa`, `brand_full`, `brand_color`, `logo_url`, ecc.
    @app.context_processor
    def _inject_branding():
        try:
            from models.config_model import AppConfig
            b = AppConfig.get_all_branding()
        except Exception:
            b = {'nome_societa': '', 'brand_color': '#3b82f6', 'logo_url': '',
                 'piva_societa': '', 'indirizzo_societa': '', 'email_societa': '',
                 'sfondo_login': '', 'setup_completato': False}
        nome_upper = (b['nome_societa'] or '').upper().strip()
        b['brand_full'] = f'PROMETEO FOR {nome_upper}' if nome_upper else 'PROMETEO'
        # Iniziali per favicon dinamica
        parole = [p for p in (b['nome_societa'] or '').replace('.', ' ').split() if p]
        b['brand_iniziali'] = (''.join(p[0] for p in parole[:2])).upper() or 'PT'
        # Counter sidebar (badge contatori)
        try:
            from models import Fattura, DDT, CostoFornitore
            from flask_login import current_user
            counters = {}
            if current_user.is_authenticated:
                counters['fatture_pending'] = Fattura.query.filter_by(stato_approvazione='pending').count()
                counters['ddt_revisione']   = DDT.query.filter_by(stato='in_revisione').count()
                counters['fatture_revisione'] = Fattura.query.filter_by(
                    tipo='passiva', riconciliazione_stato='mancante'
                ).count()
                counters['righe_rifiutate'] = CostoFornitore.query.filter_by(stato='rifiutato').count()
            b['sidebar_counters'] = counters
        except Exception:
            b['sidebar_counters'] = {}
        return b

    return app


def _migrate(db):
    """Aggiunge colonne nuove alle tabelle esistenti senza perdere dati."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)

    def _add_col(table, col, col_type):
        cols = [c['name'] for c in insp.get_columns(table)]
        if col not in cols:
            with db.engine.connect() as conn:
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}'))
                conn.commit()
            print(f'[MIGRATION] {table}.{col} aggiunta')

    if insp.has_table('commesse'):
        _add_col('commesse', 'codice_custom', 'VARCHAR(50)')

    if insp.has_table('costi_fornitori'):
        _add_col('costi_fornitori', 'dipendente_id',   'INTEGER')
        _add_col('costi_fornitori', 'mezzo_id',        'INTEGER')
        _add_col('costi_fornitori', 'fattura_id',      'INTEGER')
        _add_col('costi_fornitori', 'riga_fattura_id', 'INTEGER')
        _add_col('costi_fornitori', 'tecnico_id',      'INTEGER')
        _add_col('costi_fornitori', 'motivo_rifiuto', 'TEXT')

    if insp.has_table('righe_fattura_attiva'):
        _add_col('righe_fattura_attiva', 'commessa_id', 'INTEGER')

    if insp.has_table('righe_ddt'):
        _add_col('righe_ddt', 'articolo', 'VARCHAR(100)')

    if insp.has_table('fatture'):
        _add_col('fatture', 'riconciliazione_stato', 'VARCHAR(20)')
        _add_col('fatture', 'riconciliazione_note',  'TEXT')
