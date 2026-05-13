from datetime import date
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required
from extensions import db
from models import Fattura
from services.utils import ruolo_richiesto, ctx
from services.matching import _scadenza_bucket

bp = Blueprint('banca', __name__)


@bp.route('/banca')
@login_required
@ruolo_richiesto('banca')
def banca():
    BUCKETS = ['oggi', '5gg', '10gg', '20gg', '30gg', '60gg', '90gg', '120gg', '150gg']
    BUCKET_LABEL = {'oggi': 'Oggi', '5gg': '5 gg', '10gg': '10 gg', '20gg': '20 gg',
                    '30gg': '30 gg', '60gg': '60 gg', '90gg': '90 gg',
                    '120gg': '120 gg', '150gg': '150 gg'}
    fatture_attive_np = Fattura.query.filter_by(tipo='attiva', pagata=False).all()
    fatture_passive_np = Fattura.query.filter_by(tipo='passiva', pagata=False).all()
    timeline = {b: {'attive': 0.0, 'passive': 0.0} for b in BUCKETS}
    for f in fatture_attive_np:
        b = _scadenza_bucket(f.data_scadenza)
        if b:
            timeline[b]['attive'] += f.importo
    for f in fatture_passive_np:
        b = _scadenza_bucket(f.data_scadenza)
        if b:
            timeline[b]['passive'] += f.importo
    att_np = Fattura.query.filter_by(tipo='attiva', pagata=False).order_by(Fattura.data_scadenza).all()
    att_p = Fattura.query.filter_by(tipo='attiva', pagata=True).order_by(Fattura.data_scadenza.desc()).all()
    pas_np = Fattura.query.filter_by(tipo='passiva', pagata=False).order_by(Fattura.data_scadenza).all()
    pas_p = Fattura.query.filter_by(tipo='passiva', pagata=True).order_by(Fattura.data_scadenza.desc()).all()
    return render_template('banca/banca.html',
                           buckets=BUCKETS, bucket_label=BUCKET_LABEL, timeline=timeline,
                           att_np=att_np, att_p=att_p, pas_np=pas_np, pas_p=pas_p,
                           oggi=date.today(), **ctx())


@bp.route('/banca/fattura/<int:fattura_id>/toggle-pagamento', methods=['POST'])
@login_required
@ruolo_richiesto('banca')
def toggle_pagamento(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('banca.banca'))
    fattura.pagata = not fattura.pagata
    db.session.commit()
    stato = 'segnata come PAGATA' if fattura.pagata else 'riportata a NON PAGATA'
    flash(f'Fattura {stato}.', 'success')
    return redirect(url_for('banca.banca'))
