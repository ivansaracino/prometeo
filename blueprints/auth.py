from flask import Blueprint, render_template, redirect, url_for, request, session, flash
from flask_login import login_user, logout_user, login_required, current_user
from extensions import db, login_manager
from models import User
from config import RUOLI
from services.utils import genera_otp

bp = Blueprint('auth', __name__)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@bp.route('/healthz')
def healthz():
    return 'ok', 200


_SEZIONE_ENDPOINT = {
    'tecnico': 'tecnico.tecnico',
    'amministrazione': 'amministrazione.amministrazione',
    'banca': 'banca.banca',
    'risorse_umane': 'risorse_umane.risorse_umane',
    'owner': 'owner.owner',
    'fornitori': 'fornitori.fornitori_lista',
    'match_archivi': 'match_archivi.match_archivi',
    'tesoreria': 'tesoreria.tesoreria',
    'kpi': 'tecnico.kpi_dashboard',
    'admin_utenti': 'admin.admin_utenti',
}


@bp.route('/')
def index():
    if current_user.is_authenticated:
        sezioni = current_user.sezioni_accessibili()
        for s in sezioni:
            endpoint = _SEZIONE_ENDPOINT.get(s)
            if endpoint:
                return redirect(url_for(endpoint))
    return redirect(url_for('auth.login'))


@bp.route('/registrazione', methods=['GET', 'POST'])
def registrazione():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        conferma = request.form.get('conferma_password', '')
        telefono = request.form.get('telefono', '').strip()
        ruolo = request.form.get('ruolo', '')
        if not all([email, password, conferma, telefono, ruolo]):
            flash('Tutti i campi sono obbligatori.', 'danger')
            return render_template('auth/registrazione.html', ruoli=RUOLI)
        if password != conferma:
            flash('Le password non coincidono.', 'danger')
            return render_template('auth/registrazione.html', ruoli=RUOLI)
        if len(password) < 8:
            flash('La password deve essere di almeno 8 caratteri.', 'danger')
            return render_template('auth/registrazione.html', ruoli=RUOLI)
        if ruolo not in RUOLI:
            flash('Ruolo non valido.', 'danger')
            return render_template('auth/registrazione.html', ruoli=RUOLI)
        if User.query.filter_by(email=email).first():
            flash('Email già registrata. Effettua il login.', 'warning')
            return redirect(url_for('auth.login'))
        nome = request.form.get('nome', '').strip()
        user = User(nome=nome or None, email=email, telefono=telefono, ruolo=ruolo)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registrazione completata! Ora effettua il login.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('auth/registrazione.html', ruoli=RUOLI)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash('Email o password non corretti.', 'danger')
            return render_template('auth/login.html')
        if not user.attivo:
            flash('Il tuo account è stato disabilitato. Contatta la Direzione.', 'danger')
            return render_template('auth/login.html')
        otp = genera_otp()
        session['otp_code'] = otp
        session['otp_user_id'] = user.id
        session['otp_telefono'] = user.telefono
        return redirect(url_for('auth.verifica_otp'))
    return render_template('auth/login.html')


@bp.route('/verifica-otp', methods=['GET', 'POST'])
def verifica_otp():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))
    if 'otp_user_id' not in session:
        flash('Sessione scaduta. Rieffettua il login.', 'warning')
        return redirect(url_for('auth.login'))
    otp_generato = session.get('otp_code')
    telefono = session.get('otp_telefono', '')
    tel_mascherato = (telefono[:-4].replace(telefono[:-4], '*' * len(telefono[:-4])) + telefono[-4:]
                      if len(telefono) > 4 else telefono)
    if request.method == 'POST':
        otp_inserito = request.form.get('otp', '').strip()
        if otp_inserito == otp_generato:
            user = db.session.get(User, session['otp_user_id'])
            if user:
                login_user(user)
                session.pop('otp_code', None)
                session.pop('otp_user_id', None)
                session.pop('otp_telefono', None)
                flash(f'Benvenuto, {user.email}!', 'success')
                return redirect(url_for('auth.index'))
        else:
            flash('Codice OTP non corretto. Riprova.', 'danger')
    return render_template('auth/verifica_otp.html', tel_mascherato=tel_mascherato, otp_demo=otp_generato)


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logout effettuato con successo.', 'info')
    return redirect(url_for('auth.login'))
