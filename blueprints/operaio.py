from datetime import datetime
from flask import Blueprint, render_template, request
from extensions import db
from models import Commessa, Dipendente, CostoFornitore

bp = Blueprint('operaio', __name__)


@bp.route('/operaio')
def operaio_form():
    commesse = Commessa.query.order_by(Commessa.nome).all()
    commesse_data = [
        {
            'id': c.id, 'nome': c.nome,
            'luogo': (c.luogo or '').lower(), 'via': (c.via or '').lower(),
            'cliente': c.cliente.nome, 'cliente_id': c.cliente_id,
        }
        for c in commesse
    ]
    dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
    dipendenti_data = [
        {'id': d.id, 'nome': f'{d.cognome} {d.nome}', 'costo_orario': d.costo_orario or 0.0}
        for d in dipendenti
    ]
    return render_template('operaio/operaio_form.html',
                           commesse_data=commesse_data,
                           dipendenti_data=dipendenti_data,
                           oggi=datetime.utcnow().date())


@bp.route('/operaio/invia', methods=['POST'])
def operaio_invia():
    nome_operaio_raw = request.form.get('nome_operaio', '').strip()
    data_str         = request.form.get('data', '')

    def _get_ctx():
        commesse = Commessa.query.order_by(Commessa.nome).all()
        commesse_data = [{'id': c.id, 'nome': c.nome, 'luogo': (c.luogo or '').lower(),
                          'via': (c.via or '').lower(), 'cliente': c.cliente.nome,
                          'cliente_id': c.cliente_id} for c in commesse]
        dipendenti = Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all()
        dipendenti_data = [{'id': d.id, 'nome': f'{d.cognome} {d.nome}',
                            'costo_orario': d.costo_orario or 0.0} for d in dipendenti]
        return commesse_data, dipendenti_data

    def _err(msg):
        cd, dd = _get_ctx()
        return render_template('operaio/operaio_form.html',
                               commesse_data=cd, dipendenti_data=dd,
                               oggi=datetime.utcnow().date(), errore=msg)

    if not nome_operaio_raw:
        return _err('Inserisci il tuo nome e cognome.')

    costo_orario = 0.0
    nome_lookup  = nome_operaio_raw.lower()
    for d in Dipendente.query.order_by(Dipendente.cognome, Dipendente.nome).all():
        d_nome = f'{d.cognome} {d.nome}'.lower()
        d_inv  = f'{d.nome} {d.cognome}'.lower()
        if nome_lookup in (d_nome, d_inv) or d_nome.startswith(nome_lookup) or d_inv.startswith(nome_lookup):
            costo_orario = d.costo_orario or 0.0
            break

    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()

    nome_operaio = nome_operaio_raw
    tot_ore = 0.0
    tot_km  = 0.0
    riepilogo = []

    for i in range(1, 5):
        dove_str     = request.form.get(f'slot_dove_{i}', '').strip()
        ore_str_slot = request.form.get(f'slot_ore_{i}', '').replace(',', '.')
        km_str_slot  = request.form.get(f'slot_km_{i}', '0').replace(',', '.')
        comm_id_s    = request.form.get(f'slot_commessa_id_{i}', '').strip()
        try:
            ore_slot = float(ore_str_slot) if ore_str_slot else 0.0
        except ValueError:
            ore_slot = 0.0
        try:
            km_slot = float(km_str_slot) if km_str_slot else 0.0
        except ValueError:
            km_slot = 0.0

        if ore_slot <= 0 and km_slot <= 0:
            continue

        commessa = None
        if comm_id_s:
            try:
                commessa = db.session.get(Commessa, int(comm_id_s))
            except (ValueError, TypeError):
                pass

        comm_sug_id  = commessa.id if commessa else None
        cliente_id_s = commessa.cliente_id if commessa else None
        desc_base    = f'{nome_operaio} – {dove_str}' if dove_str else nome_operaio

        if ore_slot > 0:
            importo_ore = round(ore_slot * costo_orario, 2)
            tot_ore += ore_slot
            db.session.add(CostoFornitore(
                commessa_id=None,
                commessa_suggerita_id=comm_sug_id,
                cliente_id=cliente_id_s,
                categoria='personale', sottocategoria='ore',
                descrizione=desc_base[:300],
                quantita=ore_slot,
                valore_unitario=costo_orario if costo_orario else None,
                importo=importo_ore, stato='pending', data=data,
            ))
            label = commessa.nome if commessa else dove_str or '—'
            riepilogo.append(f'{ore_slot}h → {label}')

        if km_slot > 0:
            tariffa_km = 0.30
            tot_km += km_slot
            db.session.add(CostoFornitore(
                commessa_id=None,
                commessa_suggerita_id=comm_sug_id,
                cliente_id=cliente_id_s,
                categoria='personale', sottocategoria='km',
                descrizione=f'{desc_base} – {km_slot:.0f} km',
                quantita=km_slot, valore_unitario=tariffa_km,
                importo=round(km_slot * tariffa_km, 2),
                stato='pending', data=data,
            ))

    if tot_ore <= 0 and tot_km <= 0:
        return _err('Inserisci le ore in almeno uno dei blocchi.')

    db.session.commit()
    cantiere_str = ' | '.join(riepilogo) if riepilogo else '—'
    return render_template('operaio/operaio_ok.html',
                           nome=nome_operaio, cantiere=cantiere_str,
                           tot_ore=tot_ore, tot_km=tot_km, data=data)
