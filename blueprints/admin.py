from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from extensions import db
from models import User
from config import RUOLI
from services.utils import ruolo_richiesto, ctx

bp = Blueprint('admin', __name__)


@bp.route('/admin-utenti')
@login_required
@ruolo_richiesto('admin_utenti')
def admin_utenti():
    utenti = User.query.order_by(User.creato_il.desc()).all()
    return render_template('admin/admin_utenti.html', utenti=utenti, ruoli=RUOLI, **ctx())


@bp.route('/admin/cambia-ruolo/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def cambia_ruolo(user_id):
    if user_id == current_user.id:
        flash('Non puoi modificare il tuo stesso ruolo.', 'warning')
        return redirect(url_for('admin.admin_utenti'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.admin_utenti'))
    nuovo_ruolo = request.form.get('nuovo_ruolo', '')
    if nuovo_ruolo not in RUOLI:
        flash('Ruolo non valido.', 'danger')
        return redirect(url_for('admin.admin_utenti'))
    vecchio_ruolo = user.ruolo
    user.ruolo = nuovo_ruolo
    db.session.commit()
    flash(f'Ruolo di {user.email} cambiato da "{vecchio_ruolo}" a "{nuovo_ruolo}".', 'success')
    return redirect(url_for('admin.admin_utenti'))


@bp.route('/admin/cambia-nome/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def cambia_nome(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.admin_utenti'))
    nuovo_nome = request.form.get('nome', '').strip()
    user.nome = nuovo_nome or None
    db.session.commit()
    flash(f'Nome di {user.email} aggiornato a "{user.nome or "—"}".', 'success')
    return redirect(url_for('admin.admin_utenti'))


@bp.route('/admin/toggle-stato/<int:user_id>', methods=['POST'])
@login_required
@ruolo_richiesto('admin_utenti')
def toggle_stato(user_id):
    if user_id == current_user.id:
        flash('Non puoi disabilitare il tuo stesso account.', 'warning')
        return redirect(url_for('admin.admin_utenti'))
    user = db.session.get(User, user_id)
    if not user:
        flash('Utente non trovato.', 'danger')
        return redirect(url_for('admin.admin_utenti'))
    user.attivo = not user.attivo
    db.session.commit()
    stato = 'riabilitato' if user.attivo else 'disabilitato'
    flash(f'Account di {user.email} {stato} con successo.', 'success')
    return redirect(url_for('admin.admin_utenti'))
