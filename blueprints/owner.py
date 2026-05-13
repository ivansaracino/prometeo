from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required
from extensions import db
from models import Ordine, NodoOrganico, DDT
from services.utils import ruolo_richiesto, ctx
from services.matching import get_soglia_corrente

bp = Blueprint('owner', __name__)


@bp.route('/owner')
@login_required
@ruolo_richiesto('owner')
def owner():
    ordini_in_attesa = Ordine.query.filter_by(stato='in_attesa').count()
    nodi_count = NodoOrganico.query.count()
    soglia_corrente = get_soglia_corrente()
    n_bolle_da_approvare = DDT.query.filter_by(stato='in_attesa_tecnico').count()
    return render_template('owner/owner.html', ordini_in_attesa=ordini_in_attesa,
                           nodi_count=nodi_count, soglia_corrente=soglia_corrente,
                           n_bolle_da_approvare=n_bolle_da_approvare, **ctx())


@bp.route('/owner/ordini-in-attesa')
@login_required
@ruolo_richiesto('owner')
def owner_ordini():
    ordini = Ordine.query.filter_by(stato='in_attesa').order_by(Ordine.creato_il.desc()).all()
    return render_template('owner/owner_ordini.html', ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@bp.route('/owner/ordine/<int:ordine_id>/approva', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def approva_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('owner.owner_ordini'))
    ordine.stato = 'approvato'
    db.session.commit()
    flash(f'Ordine #{ordine.id} approvato.', 'success')
    return redirect(url_for('owner.owner_ordini'))


@bp.route('/owner/ordine/<int:ordine_id>/rifiuta', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def rifiuta_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('owner.owner_ordini'))
    ordine.stato = 'rifiutato'
    db.session.commit()
    flash(f'Ordine #{ordine.id} rifiutato.', 'warning')
    return redirect(url_for('owner.owner_ordini'))


@bp.route('/owner/organigramma')
@login_required
@ruolo_richiesto('owner')
def owner_organigramma():
    tutti_nodi = NodoOrganico.query.order_by(NodoOrganico.id).all()
    tutti_dict = [
        {
            'id':                 n.id,
            'nome':               n.nome,
            'posizione':          n.posizione,
            'soglia_approvazione': n.soglia_approvazione,
            'parent_id':          n.parent_id,
        }
        for n in tutti_nodi
    ]
    children_map = {}
    for d in tutti_dict:
        children_map.setdefault(d['parent_id'], []).append(d)
    radici = children_map.get(None, [])
    soglia_corrente = get_soglia_corrente()
    return render_template('owner/owner_organigramma.html',
                           radici=radici, tutti_nodi=tutti_dict,
                           children_map=children_map,
                           soglia_corrente=soglia_corrente, **ctx())


@bp.route('/owner/nodo/aggiungi', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def aggiungi_nodo():
    nome = request.form.get('nome', '').strip()
    posizione = request.form.get('posizione', '').strip()
    parent_id_str = request.form.get('parent_id', '').strip()
    soglia_str = request.form.get('soglia_approvazione', '').strip()
    if not nome:
        flash('Il nome del nodo è obbligatorio.', 'danger')
        return redirect(url_for('owner.owner_organigramma'))
    parent_id = int(parent_id_str) if parent_id_str else None
    soglia = None
    if soglia_str:
        try:
            soglia = float(soglia_str)
        except ValueError:
            pass
    nodo = NodoOrganico(nome=nome, posizione=posizione or None,
                        parent_id=parent_id, soglia_approvazione=soglia)
    db.session.add(nodo)
    db.session.commit()
    flash(f'Nodo "{nome}" aggiunto all\'organigramma.', 'success')
    return redirect(url_for('owner.owner_organigramma'))


@bp.route('/owner/nodo/<int:nodo_id>/modifica', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def modifica_nodo(nodo_id):
    nodo = db.session.get(NodoOrganico, nodo_id)
    if not nodo:
        flash('Nodo non trovato.', 'danger')
        return redirect(url_for('owner.owner_organigramma'))
    nome = request.form.get('nome', '').strip()
    posizione = request.form.get('posizione', '').strip()
    soglia_str = request.form.get('soglia_approvazione', '').strip()
    if not nome:
        flash('Il nome è obbligatorio.', 'danger')
        return redirect(url_for('owner.owner_organigramma'))
    nodo.nome = nome
    nodo.posizione = posizione or None
    nodo.soglia_approvazione = float(soglia_str) if soglia_str else None
    db.session.commit()
    flash(f'Nodo "{nome}" modificato.', 'success')
    return redirect(url_for('owner.owner_organigramma'))


@bp.route('/owner/nodo/<int:nodo_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('owner')
def elimina_nodo(nodo_id):
    nodo = db.session.get(NodoOrganico, nodo_id)
    if not nodo:
        flash('Nodo non trovato.', 'danger')
        return redirect(url_for('owner.owner_organigramma'))
    for figlio in nodo.figli:
        figlio.parent_id = None
    nome = nodo.nome
    db.session.delete(nodo)
    db.session.commit()
    flash(f'Nodo "{nome}" eliminato. I nodi figli sono stati spostati al livello radice.', 'info')
    return redirect(url_for('owner.owner_organigramma'))
