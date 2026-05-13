from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required
from extensions import db
from models import Dipendente, DocumentoDipendente
from config import ALERT_SCADENZA_GIORNI
from services.utils import ruolo_richiesto, ctx
from services.upload import allowed_file, salva_file, elimina_file

bp = Blueprint('risorse_umane', __name__)


@bp.route('/risorse-umane')
@login_required
@ruolo_richiesto('risorse_umane')
def risorse_umane():
    oggi = date.today()
    dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
    alert_corsi = {}
    for d in dipendenti:
        corsi_in_scadenza = DocumentoDipendente.query.filter(
            DocumentoDipendente.dipendente_id == d.id,
            DocumentoDipendente.tipo == 'corso',
            DocumentoDipendente.data_scadenza != None,
            DocumentoDipendente.data_scadenza >= oggi,
            DocumentoDipendente.data_scadenza <= oggi + timedelta(days=ALERT_SCADENZA_GIORNI)
        ).count()
        alert_corsi[d.id] = corsi_in_scadenza
    return render_template('risorse_umane/risorse_umane.html', dipendenti=dipendenti, alert_corsi=alert_corsi, **ctx())


@bp.route('/risorse-umane/nuovo-dipendente', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def nuovo_dipendente():
    nome = request.form.get('nome', '').strip()
    cognome = request.form.get('cognome', '').strip()
    if not nome or not cognome:
        flash('Nome e cognome sono obbligatori.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    db.session.add(Dipendente(nome=nome, cognome=cognome))
    db.session.commit()
    flash(f'Dipendente {nome} {cognome} aggiunto.', 'success')
    return redirect(url_for('risorse_umane.risorse_umane'))


@bp.route('/risorse-umane/elimina-dipendente/<int:dipendente_id>', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def elimina_dipendente(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    docs = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id).all()
    for doc in docs:
        elimina_file(doc.nome_file)
    db.session.delete(dipendente)
    db.session.commit()
    flash(f'Dipendente {dipendente.nome} {dipendente.cognome} eliminato.', 'info')
    return redirect(url_for('risorse_umane.risorse_umane'))


@bp.route('/risorse-umane/dipendente/<int:dipendente_id>')
@login_required
@ruolo_richiesto('risorse_umane')
def dipendente_dettaglio(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    oggi = date.today()
    corsi_scaduti = DocumentoDipendente.query.filter(
        DocumentoDipendente.dipendente_id == dipendente_id,
        DocumentoDipendente.tipo == 'corso',
        DocumentoDipendente.data_scadenza != None,
        DocumentoDipendente.data_scadenza < oggi
    ).all()
    eliminati = 0
    for corso in corsi_scaduti:
        elimina_file(corso.nome_file)
        db.session.delete(corso)
        eliminati += 1
    if eliminati:
        db.session.commit()
        flash(f'{eliminati} corso/i scaduto/i eliminato/i automaticamente.', 'warning')
    documenti = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id, tipo='documento').order_by(DocumentoDipendente.creato_il.desc()).all()
    corsi = DocumentoDipendente.query.filter_by(dipendente_id=dipendente_id, tipo='corso').order_by(DocumentoDipendente.data_scadenza).all()
    corsi_in_scadenza = [c for c in corsi if c.data_scadenza and c.data_scadenza <= oggi + timedelta(days=ALERT_SCADENZA_GIORNI)]
    tab = request.args.get('tab', 'documenti')
    return render_template('risorse_umane/dipendente_dettaglio.html',
                           dipendente=dipendente, documenti=documenti,
                           corsi=corsi, corsi_in_scadenza=corsi_in_scadenza,
                           oggi=oggi, tab=tab,
                           alert_giorni=ALERT_SCADENZA_GIORNI, **ctx())


@bp.route('/risorse-umane/dipendente/<int:dipendente_id>/aggiungi-documento', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiungi_documento(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    nome = request.form.get('nome', '').strip()
    if not nome:
        flash('Il nome del documento è obbligatorio.', 'danger')
        return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab='documenti'))
    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, f'dipendenti/{dipendente_id}/documenti')
    doc = DocumentoDipendente(dipendente_id=dipendente_id, nome=nome, nome_file=file_path, tipo='documento')
    db.session.add(doc)
    db.session.commit()
    flash('Documento aggiunto.', 'success')
    return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab='documenti'))


@bp.route('/risorse-umane/dipendente/<int:dipendente_id>/aggiungi-corso', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiungi_corso(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    nome = request.form.get('nome', '').strip()
    data_scadenza_str = request.form.get('data_scadenza', '')
    if not nome:
        flash('Il nome del corso è obbligatorio.', 'danger')
        return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab='corsi'))
    try:
        data_scadenza = datetime.strptime(data_scadenza_str, '%Y-%m-%d').date() if data_scadenza_str else None
    except ValueError:
        data_scadenza = None
    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, f'dipendenti/{dipendente_id}/corsi')
    corso = DocumentoDipendente(dipendente_id=dipendente_id, nome=nome, nome_file=file_path,
                                tipo='corso', data_scadenza=data_scadenza)
    db.session.add(corso)
    db.session.commit()
    flash('Corso aggiunto.', 'success')
    return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab='corsi'))


@bp.route('/risorse-umane/documento/<int:doc_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def elimina_documento(doc_id):
    doc = db.session.get(DocumentoDipendente, doc_id)
    if not doc:
        flash('Documento non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    dipendente_id = doc.dipendente_id
    tab = doc.tipo if doc.tipo in ('documento', 'corso') else 'documenti'
    elimina_file(doc.nome_file)
    db.session.delete(doc)
    db.session.commit()
    flash('Eliminato con successo.', 'info')
    redirect_tab = 'documenti' if tab == 'documento' else 'corsi'
    return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab=redirect_tab))


@bp.route('/risorse-umane/dipendente/<int:dipendente_id>/costo-orario', methods=['POST'])
@login_required
@ruolo_richiesto('risorse_umane')
def aggiorna_costo_orario(dipendente_id):
    dipendente = db.session.get(Dipendente, dipendente_id)
    if not dipendente:
        flash('Dipendente non trovato.', 'danger')
        return redirect(url_for('risorse_umane.risorse_umane'))
    try:
        costo = float(request.form.get('costo_orario', '0').replace(',', '.'))
    except ValueError:
        costo = 0.0
    dipendente.costo_orario = costo
    db.session.commit()
    flash(f'Costo orario aggiornato: € {costo:.2f}/ora', 'success')
    return redirect(url_for('risorse_umane.dipendente_dettaglio', dipendente_id=dipendente_id, tab='costo'))
