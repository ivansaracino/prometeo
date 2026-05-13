from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from extensions import db
from models import Fornitore
from services.utils import ruolo_richiesto, ctx

bp = Blueprint('fornitori', __name__)


@bp.route('/fornitori')
@login_required
@ruolo_richiesto('fornitori')
def fornitori_lista():
    fornitori = Fornitore.query.order_by(Fornitore.nome).all()
    return render_template('fornitori/fornitori.html', fornitori=fornitori, **ctx())


@bp.route('/fornitori/nuovo', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_nuovo():
    if request.method == 'POST':
        nome     = request.form.get('nome', '').strip()
        piva     = request.form.get('partita_iva', '').strip() or None
        cf       = request.form.get('codice_fiscale', '').strip() or None
        indirizzo = request.form.get('indirizzo', '').strip() or None
        email    = request.form.get('email', '').strip() or None
        telefono = request.form.get('telefono', '').strip() or None
        note     = request.form.get('note', '').strip() or None
        if not nome:
            flash('Il nome del fornitore è obbligatorio.', 'warning')
            return redirect(url_for('fornitori.fornitore_nuovo'))
        if piva:
            exist = Fornitore.query.filter_by(partita_iva=piva).first()
            if exist:
                flash(f'Esiste già un fornitore con P.IVA {piva}: {exist.nome}.', 'warning')
                return redirect(url_for('fornitori.fornitore_nuovo'))
        f = Fornitore(
            nome=nome, partita_iva=piva, codice_fiscale=cf,
            indirizzo=indirizzo, email=email, telefono=telefono,
            note=note, creato_da=current_user.id
        )
        db.session.add(f)
        db.session.commit()
        flash(f'Fornitore "{nome}" aggiunto.', 'success')
        return redirect(url_for('fornitori.fornitori_lista'))
    return render_template('fornitori/fornitore_form.html', fornitore=None, **ctx())


@bp.route('/fornitori/<int:fid>/modifica', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_modifica(fid):
    fornitore = db.session.get(Fornitore, fid)
    if not fornitore:
        flash('Fornitore non trovato.', 'danger')
        return redirect(url_for('fornitori.fornitori_lista'))
    if request.method == 'POST':
        fornitore.nome           = request.form.get('nome', '').strip()
        fornitore.partita_iva    = request.form.get('partita_iva', '').strip() or None
        fornitore.codice_fiscale = request.form.get('codice_fiscale', '').strip() or None
        fornitore.indirizzo      = request.form.get('indirizzo', '').strip() or None
        fornitore.email          = request.form.get('email', '').strip() or None
        fornitore.telefono       = request.form.get('telefono', '').strip() or None
        fornitore.note           = request.form.get('note', '').strip() or None
        if not fornitore.nome:
            flash('Il nome è obbligatorio.', 'warning')
            return redirect(url_for('fornitori.fornitore_modifica', fid=fid))
        db.session.commit()
        flash(f'Fornitore "{fornitore.nome}" aggiornato.', 'success')
        return redirect(url_for('fornitori.fornitori_lista'))
    return render_template('fornitori/fornitore_form.html', fornitore=fornitore, **ctx())


@bp.route('/fornitori/<int:fid>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('fornitori')
def fornitore_elimina(fid):
    fornitore = db.session.get(Fornitore, fid)
    if not fornitore:
        flash('Fornitore non trovato.', 'danger')
        return redirect(url_for('fornitori.fornitori_lista'))
    nome = fornitore.nome
    db.session.delete(fornitore)
    db.session.commit()
    flash(f'Fornitore "{nome}" eliminato.', 'info')
    return redirect(url_for('fornitori.fornitori_lista'))
