import io
import os
import json
from datetime import datetime, date, timedelta
from flask import (Blueprint, render_template, redirect, url_for,
                   request, flash, send_file, jsonify)
from flask_login import login_required, current_user
from sqlalchemy import text as _text
from extensions import db
from models import (Cliente, Commessa, CostoFornitore, Ordine, RigaOrdine,
                    Fattura, RigaFatturaAttiva, DDT, RigaDDT, NodoOrganico,
                    AbbinatoOrdineSubappalto, User)
from services.utils import ruolo_richiesto, ctx
from services.matching import get_soglia_corrente, _abbina_ordine_subappalto

bp = Blueprint('tecnico', __name__)


def _next_commessa_numero(cliente_id):
    max_num = db.session.query(db.func.max(Commessa.numero)).filter_by(cliente_id=cliente_id).scalar()
    return (max_num or 0) + 1


def _next_cliente_numero():
    max_num = db.session.query(db.func.max(Cliente.numero)).scalar()
    return (max_num or 0) + 1


def _check_cliente_access(cliente):
    if current_user.ruolo == 'Tecnico':
        return cliente and cliente.owner == current_user.nome
    return bool(cliente)


@bp.route('/tecnico')
@login_required
@ruolo_richiesto('tecnico')
def tecnico():
    q = Cliente.query
    if current_user.ruolo == 'Tecnico':
        q = q.filter(Cliente.owner == current_user.nome)
    clienti = q.order_by(Cliente.nome).all()
    responsabili = NodoOrganico.query.filter(
        NodoOrganico.posizione.ilike('%direttore tecnico%')
    ).order_by(NodoOrganico.nome).all()
    n_fatture_pending = Fattura.query.filter_by(tipo='passiva', stato_approvazione='pending').count()
    if current_user.ruolo == 'Direzione':
        n_bolle_da_approvare = DDT.query.filter_by(stato='in_attesa_tecnico').count()
    else:
        n_bolle_da_approvare = DDT.query.filter_by(
            stato='in_attesa_tecnico', tecnico_id=current_user.id
        ).count()
    return render_template('tecnico/tecnico.html', clienti=clienti, responsabili=responsabili,
                           n_fatture_pending=n_fatture_pending,
                           n_bolle_da_approvare=n_bolle_da_approvare, **ctx())


@bp.route('/api/next-commessa-numero/<int:cliente_id>')
@login_required
def api_next_commessa_numero(cliente_id):
    return jsonify({'numero': _next_commessa_numero(cliente_id)})


@bp.route('/api/commesse-per-cliente/<int:cliente_id>')
@login_required
def api_commesse_per_cliente(cliente_id):
    commesse = Commessa.query.filter_by(cliente_id=cliente_id).order_by(Commessa.numero).all()
    return jsonify([{'id': c.id, 'numero': c.numero, 'nome': c.nome} for c in commesse])


@bp.route('/tecnico/nuovo-cliente', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def nuovo_cliente():
    nome  = request.form.get('nome', '').strip()
    piva  = request.form.get('partita_iva', '').strip() or None
    cig   = request.form.get('cig', '').strip() or None
    cup   = request.form.get('cup', '').strip() or None
    owner = request.form.get('owner', '').strip() or None
    c_nome  = request.form.get('commessa_nome', '').strip() or 'Commessa principale'
    c_luogo = request.form.get('commessa_luogo', '').strip() or None
    c_via   = request.form.get('commessa_via',   '').strip() or None
    c_num   = request.form.get('commessa_numero', '').strip()
    if not nome:
        flash('Il nome del cliente è obbligatorio.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    if Cliente.query.filter_by(nome=nome).first():
        flash(f'Esiste già un cliente con il nome "{nome}".', 'warning')
        return redirect(url_for('tecnico.tecnico'))
    num_cliente = _next_cliente_numero()
    cliente = Cliente(nome=nome, numero=num_cliente, partita_iva=piva,
                      cig=cig, cup=cup, owner=owner)
    db.session.add(cliente)
    db.session.flush()
    try:
        num_commessa = int(c_num) if c_num else _next_commessa_numero(cliente.id)
    except ValueError:
        num_commessa = _next_commessa_numero(cliente.id)
    db.session.add(Commessa(
        cliente_id=cliente.id, numero=num_commessa,
        nome=c_nome, luogo=c_luogo, via=c_via, owner=owner
    ))
    db.session.commit()
    tipo = 'Pubblico (CIG/CUP)' if (cig or cup) else 'Privato'
    flash(f'Cliente C{num_cliente:03d} "{nome}" ({tipo}) e commessa N°{num_commessa} "{c_nome}" creati.', 'success')
    return redirect(url_for('tecnico.lista_commesse', cliente_id=cliente.id))


@bp.route('/tecnico/elimina-cliente/<int:cliente_id>', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_cliente(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    db.session.delete(cliente)
    db.session.commit()
    flash(f'Cliente "{cliente.nome}" eliminato.', 'info')
    return redirect(url_for('tecnico.tecnico'))


@bp.route('/tecnico/cliente/<int:cliente_id>')
@login_required
@ruolo_richiesto('tecnico')
def lista_commesse(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente or not _check_cliente_access(cliente):
        flash('Cliente non trovato o accesso non autorizzato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    responsabili = NodoOrganico.query.filter(
        NodoOrganico.posizione.ilike('%direttore tecnico%')
    ).order_by(NodoOrganico.nome).all()
    return render_template('tecnico/cliente_commesse.html', cliente=cliente,
                           responsabili=responsabili, **ctx())


@bp.route('/tecnico/cliente/<int:cliente_id>/nuova-commessa', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def nuova_commessa(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    nome  = request.form.get('nome', '').strip() or 'Nuova Commessa'
    luogo = request.form.get('luogo', '').strip() or None
    via   = request.form.get('via', '').strip() or None
    owner = request.form.get('owner', '').strip() or None
    num = _next_commessa_numero(cliente_id)
    db.session.add(Commessa(cliente_id=cliente_id, numero=num,
                            nome=nome, luogo=luogo, via=via, owner=owner))
    db.session.commit()
    flash(f'Commessa n°{num} "{nome}" aggiunta.', 'success')
    return redirect(url_for('tecnico.lista_commesse', cliente_id=cliente_id))


@bp.route('/tecnico/commessa/<int:commessa_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_commessa(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    cliente_id = commessa.cliente_id
    nome = commessa.nome
    db.session.delete(commessa)
    db.session.commit()
    flash(f'Commessa "{nome}" eliminata.', 'info')
    return redirect(url_for('tecnico.lista_commesse', cliente_id=cliente_id))


@bp.route('/kpi')
@login_required
@ruolo_richiesto('kpi')
def kpi_dashboard():
    commesse = Commessa.query.order_by(Commessa.cliente_id, Commessa.numero).all()
    righe = []
    for commessa in commesse:
        c_mat  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='materiali').all())
        c_sub  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='subappalti').all())
        c_per  = sum(v.importo for v in CostoFornitore.query.filter_by(commessa_id=commessa.id, categoria='personale').all())
        passivo = c_mat + c_sub + c_per
        fatture_att = Fattura.query.filter_by(tipo='attiva', commessa_id=commessa.id).all()
        ordini_comm = Ordine.query.filter_by(commessa_id=commessa.id).all()
        attivo = sum(f.importo for f in fatture_att) + sum(o.totale or 0 for o in ordini_comm)
        denominatore_k1 = c_sub + c_mat + c_per
        if denominatore_k1 > 0:
            k1 = attivo / denominatore_k1
            if k1 < 1.0:
                k1_label, k1_color = 'PERDITA', 'danger'
            elif k1 < 1.15:
                k1_label, k1_color = 'Scarso guadagno', 'warning'
            else:
                k1_label, k1_color = 'OK', 'success'
        else:
            k1, k1_label, k1_color = None, '–', 'secondary'
        k2 = round(c_per  / passivo * 100, 1) if passivo > 0 else None
        k3 = round(c_mat  / passivo * 100, 1) if passivo > 0 else None
        k4 = round(c_sub  / passivo * 100, 1) if passivo > 0 else None
        righe.append({
            'commessa': commessa, 'codice': commessa.codice, 'nome': commessa.nome,
            'attivo': attivo, 'passivo': passivo,
            'c_mat': c_mat, 'c_sub': c_sub, 'c_per': c_per,
            'k1': round(k1, 2) if k1 is not None else None,
            'k1_label': k1_label, 'k1_color': k1_color,
            'k2': k2, 'k3': k3, 'k4': k4,
        })
    righe.sort(key=lambda r: (r['passivo'] == 0 and r['attivo'] == 0, r['codice']))
    return render_template('tecnico/kpi.html', righe=righe, **ctx())


@bp.route('/tecnico/commessa/<int:commessa_id>')
@login_required
@ruolo_richiesto('tecnico')
def commessa_detail(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa or not _check_cliente_access(commessa.cliente):
        flash('Commessa non trovata o accesso non autorizzato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    cliente = commessa.cliente
    categorie = ['materiali', 'subappalti', 'personale']
    costi = {}
    totali = {}
    for cat in categorie:
        voci = CostoFornitore.query.filter_by(commessa_id=commessa_id, categoria=cat).order_by(CostoFornitore.data.desc()).all()
        costi[cat] = voci
        totali[cat] = sum(v.importo for v in voci)
    tab    = request.args.get('tab',    'materiali')
    subtab = request.args.get('subtab', 'ore')
    sub_totali = {}
    for sub in ['ore', 'pasti', 'km']:
        sub_totali[sub] = sum(v.importo for v in costi['personale'] if v.sottocategoria == sub)
    fatture_passive = Fattura.query.filter_by(
        tipo='passiva', commessa_id=commessa_id
    ).order_by(Fattura.data_scadenza).all()
    fatture_attive = Fattura.query.filter_by(
        tipo='attiva', commessa_id=commessa_id
    ).order_by(Fattura.data_scadenza).all()
    ordini_commessa = Ordine.query.filter_by(
        commessa_id=commessa_id
    ).order_by(Ordine.creato_il.desc()).all()
    oggi_d = date.today()
    totale_fatture_passive = sum(f.importo for f in fatture_passive)
    totale_fatture_attive  = sum((f.importo_netto or f.importo) for f in fatture_attive)
    ordini_tot = sum(o.totale or 0 for o in cliente.ordini)
    riepilogo = {
        'attivo_ordini':      round(ordini_tot, 2),
        'attivo_fatture':     round(totale_fatture_attive, 2),
        'attivo':             round(ordini_tot + totale_fatture_attive, 2),
        'passivo_materiali':  round(totali['materiali'], 2),
        'passivo_subappalti': round(totali['subappalti'], 2),
        'passivo_personale':  round(totali['personale'], 2),
        'passivo_fatture':    round(totale_fatture_passive, 2),
    }
    ddt_in_attesa = DDT.query.filter_by(
        commessa_id=commessa_id, stato='in_attesa_tecnico'
    ).order_by(DDT.creato_il.desc()).all()
    righe_ddt_commessa = (RigaDDT.query
                          .filter_by(commessa_id=commessa_id)
                          .order_by(RigaDDT.ddt_id, RigaDDT.id)
                          .all())
    righe_per_ddt = {}
    for riga in righe_ddt_commessa:
        righe_per_ddt.setdefault(riga.ddt_id, []).append(riga)
    totale_righe_ddt = sum(r.calc_importo() for r in righe_ddt_commessa)
    return render_template('tecnico/costi_fornitori.html',
                           commessa=commessa, cliente=cliente,
                           costi=costi, totali=totali,
                           tab=tab, subtab=subtab, sub_totali=sub_totali,
                           fatture_passive=fatture_passive,
                           fatture_attive=fatture_attive,
                           ordini_commessa=ordini_commessa,
                           riepilogo=riepilogo,
                           ddt_in_attesa=ddt_in_attesa,
                           righe_ddt_commessa=righe_ddt_commessa,
                           righe_per_ddt=righe_per_ddt,
                           totale_righe_ddt=totale_righe_ddt,
                           oggi=oggi_d, **ctx())


@bp.route('/tecnico/cliente/<int:cliente_id>/costi')
@login_required
@ruolo_richiesto('tecnico')
def costi_fornitori(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if cliente and cliente.commesse:
        return redirect(url_for('tecnico.commessa_detail', commessa_id=cliente.commesse[0].id))
    return redirect(url_for('tecnico.lista_commesse', cliente_id=cliente_id))


@bp.route('/tecnico/commessa/<int:commessa_id>/aggiungi-costo', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def aggiungi_costo(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    categoria      = request.form.get('categoria', '')
    sottocategoria = request.form.get('sottocategoria', '') or None
    descrizione    = request.form.get('descrizione', '').strip()
    importo_str    = request.form.get('importo', '0').replace(',', '.')
    quantita_str   = request.form.get('quantita', '').replace(',', '.')
    valore_str     = request.form.get('valore_unitario', '').replace(',', '.')
    data_str       = request.form.get('data', '')
    if not descrizione or not categoria:
        flash('Descrizione e categoria sono obbligatorie.', 'danger')
        return redirect(url_for('tecnico.commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))
    quantita        = float(quantita_str) if quantita_str else None
    valore_unitario = float(valore_str)   if valore_str   else None
    if quantita is not None and valore_unitario is not None:
        importo = quantita * valore_unitario
    else:
        try:
            importo = float(importo_str)
        except ValueError:
            importo = 0.0
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()
    voce = CostoFornitore(
        commessa_id=commessa_id, cliente_id=commessa.cliente_id,
        categoria=categoria, sottocategoria=sottocategoria,
        descrizione=descrizione, quantita=quantita, valore_unitario=valore_unitario,
        importo=importo, data=data
    )
    db.session.add(voce)
    db.session.commit()
    flash('Voce aggiunta con successo.', 'success')
    return redirect(url_for('tecnico.commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))


@bp.route('/tecnico/costo/<int:costo_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_costo(costo_id):
    voce = db.session.get(CostoFornitore, costo_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    commessa_id    = voce.commessa_id
    categoria      = voce.categoria
    sottocategoria = voce.sottocategoria
    db.session.delete(voce)
    db.session.commit()
    flash('Voce eliminata.', 'info')
    if commessa_id:
        return redirect(url_for('tecnico.commessa_detail', commessa_id=commessa_id, tab=categoria, subtab=sottocategoria))
    return redirect(url_for('tecnico.tecnico'))


@bp.route('/tecnico/tutti-ordini')
@login_required
@ruolo_richiesto('tecnico')
def tutti_ordini():
    clienti_acc = [c for c in Cliente.query.order_by(Cliente.nome).all() if _check_cliente_access(c)]
    clienti_ids = [c.id for c in clienti_acc]
    ordini = Ordine.query.filter(Ordine.cliente_id.in_(clienti_ids)).order_by(Ordine.creato_il.desc()).all()
    return render_template('tecnico/tutti_ordini.html', ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@bp.route('/tecnico/cliente/<int:cliente_id>/ordini')
@login_required
@ruolo_richiesto('tecnico')
def ordini_lista(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    ordini = Ordine.query.filter_by(cliente_id=cliente_id).order_by(Ordine.creato_il.desc()).all()
    return render_template('tecnico/ordini_lista.html', cliente=cliente, ordini=ordini, soglia=get_soglia_corrente(), **ctx())


@bp.route('/tecnico/cliente/<int:cliente_id>/ordini/nuovo', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('tecnico')
def ordine_nuovo(cliente_id):
    cliente = db.session.get(Cliente, cliente_id)
    if not cliente:
        flash('Cliente non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    commessa_id_pre = request.args.get('commessa_id') or request.form.get('commessa_id_pre')
    commessa_pre = None
    if commessa_id_pre:
        try:
            commessa_pre = db.session.get(Commessa, int(commessa_id_pre))
        except (ValueError, TypeError):
            pass
    if request.method == 'POST':
        commessa_id_form = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_form:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_form))
            except (ValueError, TypeError):
                pass
        identificativo = ''
        if commessa:
            num_cl = cliente.numero or cliente.id
            identificativo = f'C{num_cl:03d}-{commessa.numero}'
        ordine = Ordine(
            cliente_id=cliente_id,
            commessa_id=commessa.id if commessa else None,
            identificativo=identificativo,
            azienda=request.form.get('azienda', '').strip(),
            piva=request.form.get('piva', '').strip(),
            rif_ordine=request.form.get('rif_ordine', '').strip() or identificativo,
            dati_cantiere=request.form.get('dati_cantiere', '').strip(),
            indirizzo=request.form.get('indirizzo', '').strip(),
            rif_cantiere=request.form.get('rif_cantiere', '').strip(),
            note=request.form.get('note', '').strip(),
            cig=request.form.get('cig', '').strip() or None,
            cup=request.form.get('cup', '').strip() or None,
            creato_da=current_user.id
        )
        db.session.add(ordine)
        db.session.flush()
        descrizioni = request.form.getlist('descrizione[]')
        qta_list = request.form.getlist('qta[]')
        um_list = request.form.getlist('um[]')
        prezzi = request.form.getlist('prezzo_unitario[]')
        totale_ordine = 0.0
        for i, desc in enumerate(descrizioni):
            if not desc.strip():
                continue
            try:
                qta = float(qta_list[i].replace(',', '.')) if i < len(qta_list) else 0
                prezzo = float(prezzi[i].replace(',', '.')) if i < len(prezzi) else 0
            except (ValueError, IndexError):
                qta, prezzo = 0, 0
            tot_riga = qta * prezzo
            totale_ordine += tot_riga
            db.session.add(RigaOrdine(ordine_id=ordine.id, numero=i+1, descrizione=desc.strip(),
                                       qta=qta, unita_misura=um_list[i] if i < len(um_list) else '',
                                       prezzo_unitario=prezzo, totale=tot_riga))
        ordine.totale = totale_ordine
        soglia = get_soglia_corrente()
        if totale_ordine > soglia:
            ordine.stato = 'in_attesa'
            flash(f'Ordine creato. Totale € {totale_ordine:,.2f} — supera la soglia di € {soglia:,.0f}: in attesa approvazione Responsabili.', 'warning')
        else:
            ordine.stato = 'approvato'
            flash(f'Ordine approvato automaticamente. Totale: € {totale_ordine:,.2f}', 'success')
        db.session.commit()
        return redirect(url_for('tecnico.ordini_lista', cliente_id=cliente_id))
    commesse_cliente = Commessa.query.filter_by(cliente_id=cliente_id).order_by(Commessa.numero).all()
    return render_template('tecnico/ordine_nuovo.html', cliente=cliente,
                           commesse_cliente=commesse_cliente, commessa_pre=commessa_pre,
                           soglia=get_soglia_corrente(), **ctx())


@bp.route('/tecnico/ordine/<int:ordine_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def elimina_ordine(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    cliente_id = ordine.cliente_id
    cod = f'ORD-{ordine.id:04d}'
    db.session.delete(ordine)
    db.session.commit()
    flash(f'Ordine {cod} eliminato.', 'info')
    return redirect(url_for('tecnico.ordini_lista', cliente_id=cliente_id))


@bp.route('/tecnico/ordine/<int:ordine_id>')
@login_required
@ruolo_richiesto('tecnico')
def ordine_dettaglio(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    return render_template('tecnico/ordine_dettaglio.html', ordine=ordine, cliente=ordine.cliente,
                           soglia=get_soglia_corrente(), **ctx())


@bp.route('/tecnico/ordine/<int:ordine_id>/pdf')
@login_required
@ruolo_richiesto('tecnico')
def ordine_pdf(ordine_id):
    ordine = db.session.get(Ordine, ordine_id)
    if not ordine:
        flash('Ordine non trovato.', 'danger')
        return redirect(url_for('tecnico.tecnico'))

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, HRFlowable)

    C_DARK   = colors.HexColor('#1e1b4b')
    C_MID    = colors.HexColor('#4338ca')
    C_LIGHT  = colors.HexColor('#e0e7ff')
    C_ACCENT = colors.HexColor('#6366f1')
    C_GREY   = colors.HexColor('#64748b')
    C_ROW    = colors.HexColor('#f5f3ff')
    C_WHITE  = colors.white
    C_BLACK  = colors.HexColor('#0f172a')

    def ps(name, fontName='Helvetica-Bold', fontSize=10, textColor=C_BLACK,
           alignment=TA_LEFT, leading=None, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=fontName, fontSize=fontSize,
                              textColor=textColor, alignment=alignment,
                              leading=leading or fontSize * 1.3,
                              spaceBefore=spaceBefore, spaceAfter=spaceAfter)

    sty_brand_big   = ps('BB',  'Helvetica-Bold',  33, C_WHITE, TA_LEFT)
    sty_brand_sub   = ps('BS',  'Helvetica-Bold',  9,  colors.HexColor('#fbbf24'), TA_LEFT)
    sty_label       = ps('LB',  'Helvetica',        7, C_GREY,  TA_LEFT)
    sty_value       = ps('VL',  'Helvetica-Bold',   9, C_BLACK, TA_LEFT)
    sty_ordine_num  = ps('ON',  'Helvetica-Bold',  13, C_DARK,  TA_LEFT)
    sty_th          = ps('TH',  'Helvetica-Bold',   8, C_WHITE, TA_CENTER)
    sty_td          = ps('TD',  'Helvetica',         8, C_BLACK, TA_LEFT)
    sty_td_r        = ps('TDR', 'Helvetica',         8, C_BLACK, TA_RIGHT)
    sty_td_bold     = ps('TDB', 'Helvetica-Bold',   8, C_BLACK, TA_RIGHT)
    sty_footer      = ps('FT',  'Helvetica',         7, C_GREY,  TA_CENTER)
    sty_note_lbl    = ps('NL',  'Helvetica-Bold',   8, C_GREY,  TA_LEFT)
    sty_note_val    = ps('NV',  'Helvetica',         8, C_BLACK, TA_LEFT)

    buf = io.BytesIO()
    margin = 1.8 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin)
    story = []

    logo_inner = Table(
        [[Paragraph('<font color="#818cf8">ARTE</font><font color="#ffffff">DILE</font>',
                    sty_brand_big)],
         [Paragraph('&#9656;  Impianti ed Edilizia', sty_brand_sub)]],
        colWidths=[7.5 * cm], rowHeights=[1.9 * cm, 0.75 * cm]
    )
    logo_inner.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#0d1117')),
        ('TOPPADDING',    (0, 0), (0, 0),  14),
        ('BOTTOMPADDING', (0, 1), (0, 1),  13),
        ('LEFTPADDING',   (0, 0), (-1, -1), 18),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 18),
        ('ROUNDEDCORNERS', [10]),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW',     (0, 0), (0, 0),  0.8, colors.HexColor('#818cf8')),
    ]))
    ord_num = Paragraph(f'ORDINE  ORD-{ordine.id:04d}', sty_ordine_num)
    data_ord = Paragraph(ordine.creato_il.strftime('%d %B %Y').upper(), ps('DT', 'Helvetica', 8, C_GREY))
    logo_col = Table([[logo_inner], [Spacer(1, 6)], [ord_num], [data_ord]], colWidths=[7 * cm])
    logo_col.setStyle(TableStyle([('LEFTPADDING', (0,0), (-1,-1), 0), ('RIGHTPADDING', (0,0), (-1,-1), 0), ('TOPPADDING', (0,0), (-1,-1), 0), ('BOTTOMPADDING', (0,0), (-1,-1), 2)]))

    def ref_row(label, value):
        return [Paragraph(label, sty_label), Paragraph(str(value or '—'), sty_value)]

    ref_data = [
        ref_row('CLIENTE',       ordine.cliente.nome if ordine.cliente else '—'),
        ref_row('AZIENDA',       ordine.azienda),
        ref_row('P.IVA',         ordine.piva),
        ref_row('RIF. ORDINE',   ordine.rif_ordine),
        ref_row('CANTIERE',      ordine.dati_cantiere),
        ref_row('INDIRIZZO',     ordine.indirizzo),
        ref_row('RIF. CANTIERE', ordine.rif_cantiere),
    ]
    ref_table = Table(ref_data, colWidths=[2.8 * cm, 6.6 * cm])
    ref_table.setStyle(TableStyle([
        ('VALIGN',       (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',   (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('LINEBELOW',    (0, 0), (-1, -2), 0.3, colors.HexColor('#e0e7ff')),
    ]))
    header = Table([[logo_col, ref_table]], colWidths=[8 * cm, 9.8 * cm])
    header.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 12))
    story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=12))

    col_w = [1.0*cm, 7.5*cm, 1.5*cm, 1.5*cm, 2.8*cm, 2.8*cm]
    table_data = [[
        Paragraph('N.',         sty_th),
        Paragraph('Descrizione',sty_th),
        Paragraph('Qtà',        sty_th),
        Paragraph('U.M.',       sty_th),
        Paragraph('Prezzo Unit.',sty_th),
        Paragraph('Totale',     sty_th),
    ]]
    for i, riga in enumerate(ordine.righe):
        bg = C_ROW if i % 2 == 1 else C_WHITE
        table_data.append([
            Paragraph(str(riga.numero), ps(f'n{i}', 'Helvetica', 8, C_GREY, TA_CENTER)),
            Paragraph(riga.descrizione or '', sty_td),
            Paragraph(f'{riga.qta:g}',         ps(f'q{i}', 'Helvetica', 8, C_BLACK, TA_CENTER)),
            Paragraph(riga.unita_misura or '',  ps(f'u{i}', 'Helvetica', 8, C_GREY,  TA_CENTER)),
            Paragraph(f'€ {riga.prezzo_unitario:,.2f}', sty_td_r),
            Paragraph(f'€ {riga.totale:,.2f}',          sty_td_bold),
        ])
    table_data.append([
        Paragraph('', sty_th), Paragraph('', sty_th),
        Paragraph('', sty_th), Paragraph('', sty_th),
        Paragraph('TOTALE ORDINE', ps('TL', 'Helvetica-Bold', 9, C_DARK, TA_RIGHT)),
        Paragraph(f'€ {ordine.totale:,.2f}', ps('TV', 'Helvetica-Bold', 11, C_ACCENT, TA_RIGHT)),
    ])
    n_righe = len(ordine.righe)
    voci_table = Table(table_data, colWidths=col_w, repeatRows=1)
    voci_table.setStyle(TableStyle([
        ('BACKGROUND',   (0, 0), (-1, 0),  C_DARK),
        ('TEXTCOLOR',    (0, 0), (-1, 0),  C_WHITE),
        ('ROWBACKGROUND',(0, 1), (-1, n_righe), [C_WHITE, C_ROW]),
        ('BACKGROUND',   (0, -1), (-1, -1), C_LIGHT),
        ('LINEABOVE',    (0, -1), (-1, -1), 1.5, C_ACCENT),
        ('TOPPADDING',   (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 7),
        ('LEFTPADDING',  (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        ('INNERGRID',    (0, 1), (-1, -2), 0.3, colors.HexColor('#e0e7ff')),
        ('BOX',          (0, 0), (-1, -1), 0.5, C_MID),
        ('ROUNDEDCORNERS', [4]),
    ]))
    story.append(voci_table)

    if ordine.note:
        story.append(Spacer(1, 14))
        note_box = Table(
            [[Paragraph('NOTE', sty_note_lbl)],
             [Paragraph(ordine.note, sty_note_val)]],
            colWidths=[doc.width]
        )
        note_box.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#faf5ff')),
            ('BOX',           (0, 0), (-1, -1), 0.5, colors.HexColor('#c7d2fe')),
            ('TOPPADDING',    (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('LEFTPADDING',   (0, 0), (-1, -1), 10),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
            ('ROUNDEDCORNERS', [6]),
        ]))
        story.append(note_box)

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_LIGHT, spaceAfter=6))
    story.append(Paragraph(
        f'ARTEDILE  ·  Documento generato il {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}  ·  ORD-{ordine.id:04d}',
        sty_footer
    ))
    doc.build(story)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'Ordine_ARTEDILE_{ordine.id:04d}.pdf', as_attachment=True)


@bp.route('/tecnico/approvazioni-ore')
@login_required
@ruolo_richiesto('tecnico')
def approvazioni_ore():
    voci = (CostoFornitore.query
            .filter_by(categoria='personale', stato='pending')
            .order_by(CostoFornitore.data.desc()).all())
    commesse_all = Commessa.query.order_by(Commessa.nome).all()
    tot_ore = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'ore')
    tot_km  = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'km')
    return render_template('tecnico/approvazioni_ore.html',
                           voci=voci, commesse_all=commesse_all,
                           tot_ore=tot_ore, tot_km=tot_km, **ctx())


@bp.route('/tecnico/approvazioni-ore/<int:voce_id>/azione', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def azione_approvazione_ora(voce_id):
    voce = db.session.get(CostoFornitore, voce_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('tecnico.approvazioni_ore'))
    azione = request.form.get('azione', '')
    if azione == 'approva':
        nuova_q_str = request.form.get('nuova_quantita', '').strip()
        try:
            nuova_q = float(nuova_q_str) if nuova_q_str else (voce.quantita or 0)
        except ValueError:
            nuova_q = voce.quantita or 0
        commessa_id_s = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_s:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_s))
            except (ValueError, TypeError):
                pass
        if not commessa:
            sug_id = voce.commessa_suggerita_id
            if sug_id:
                commessa = db.session.get(Commessa, sug_id)
        if not commessa:
            flash('Seleziona una commessa per approvare.', 'warning')
            return redirect(url_for('tecnico.approvazioni_ore'))
        nuovo_importo = voce.importo or 0
        if voce.valore_unitario:
            nuovo_importo = round(nuova_q * voce.valore_unitario, 2)
        db.session.execute(_text(
            "UPDATE costi_fornitori SET quantita=:q, importo=:imp, "
            "commessa_id=:cid, cliente_id=:klid, stato='approvato' "
            "WHERE id=:vid"
        ), dict(q=nuova_q, imp=nuovo_importo, cid=commessa.id, klid=commessa.cliente_id, vid=voce_id))
        db.session.commit()
        label = 'ore' if voce.sottocategoria == 'ore' else 'km'
        flash(f'{nuova_q} {label} approvati → {commessa.cliente.nome} / {commessa.nome}.', 'success')
    elif azione == 'rifiuta':
        db.session.execute(_text(
            "UPDATE costi_fornitori SET stato='rifiutato' WHERE id=:vid"
        ), dict(vid=voce_id))
        db.session.commit()
        flash('Voce rifiutata e spostata in "Ore non allocate".', 'info')
    return redirect(url_for('tecnico.approvazioni_ore'))


@bp.route('/tecnico/ore-non-allocate')
@login_required
@ruolo_richiesto('tecnico')
def ore_non_allocate():
    voci = (CostoFornitore.query
            .filter_by(categoria='personale', stato='rifiutato')
            .order_by(CostoFornitore.data.desc()).all())
    commesse_all = Commessa.query.order_by(Commessa.nome).all()
    tot_ore     = sum(v.quantita or 0 for v in voci if v.sottocategoria == 'ore')
    tot_importo = sum(v.importo  or 0 for v in voci)
    return render_template('tecnico/ore_non_allocate.html',
                           voci=voci, commesse_all=commesse_all,
                           tot_ore=tot_ore, tot_importo=tot_importo, **ctx())


@bp.route('/tecnico/ore-non-allocate/<int:voce_id>/assegna', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def assegna_ora_non_allocata(voce_id):
    commessa_id_str = request.form.get('commessa_id', '').strip()
    nuova_q_str     = request.form.get('nuova_quantita', '').strip()
    try:
        commessa = db.session.get(Commessa, int(commessa_id_str))
    except (ValueError, TypeError):
        commessa = None
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico.ore_non_allocate'))
    voce = db.session.get(CostoFornitore, voce_id)
    if not voce:
        flash('Voce non trovata.', 'danger')
        return redirect(url_for('tecnico.ore_non_allocate'))
    try:
        nuova_q = float(nuova_q_str) if nuova_q_str else (voce.quantita or 0)
    except ValueError:
        nuova_q = voce.quantita or 0
    nuovo_importo = voce.importo or 0
    if voce.valore_unitario and nuova_q:
        nuovo_importo = round(nuova_q * voce.valore_unitario, 2)
    db.session.execute(_text(
        "UPDATE costi_fornitori SET quantita=:q, importo=:imp, "
        "commessa_id=:cid, cliente_id=:klid, stato='approvato' "
        "WHERE id=:vid"
    ), dict(q=nuova_q, imp=nuovo_importo, cid=commessa.id, klid=commessa.cliente_id, vid=voce_id))
    db.session.commit()
    label = 'ore' if voce.sottocategoria == 'ore' else 'km'
    flash(f'{nuova_q} {label} assegnati → {commessa.cliente.nome} / {commessa.nome}.', 'success')
    return redirect(url_for('tecnico.ore_non_allocate'))


@bp.route('/tecnico/approvazioni-fatture')
@login_required
@ruolo_richiesto('tecnico')
def approvazioni_fatture():
    fatture = (Fattura.query
               .filter_by(tipo='passiva', stato_approvazione='pending')
               .order_by(Fattura.creato_il.desc()).all())
    commesse_all = (Commessa.query
                    .join(Cliente)
                    .order_by(Cliente.nome, Commessa.numero)
                    .all())
    tot_importo = sum(f.importo or 0 for f in fatture)
    return render_template('tecnico/approvazioni_fatture.html',
                           fatture=fatture, commesse_all=commesse_all,
                           tot_importo=tot_importo, **ctx())


@bp.route('/tecnico/approvazioni-fatture/<int:fattura_id>/azione', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def azione_approvazione_fattura(fattura_id):
    import zipfile as zf_mod
    from config import UPLOAD_ROOT
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('tecnico.approvazioni_fatture'))
    azione = request.form.get('azione', '')
    if azione == 'approva':
        commessa_id_s = request.form.get('commessa_id', '').strip()
        commessa = None
        if commessa_id_s:
            try:
                commessa = db.session.get(Commessa, int(commessa_id_s))
            except (ValueError, TypeError):
                pass
        if not commessa and fattura.commessa_id:
            commessa = db.session.get(Commessa, fattura.commessa_id)
        if not commessa:
            flash('Seleziona una commessa per approvare la fattura.', 'warning')
            return redirect(url_for('tecnico.approvazioni_fatture'))
        fattura.commessa_id = commessa.id
        fattura.cliente_id  = commessa.cliente_id
        if fattura.sottotipo == 'subappalto':
            _stato_ab, _, _, _ordine_ab = _abbina_ordine_subappalto(
                fattura, commessa, current_user.id, solo_check=True
            )
            if _stato_ab == 'allarme':
                _delta = (fattura.importo or 0) - (_ordine_ab.totale if _ordine_ab else 0)
                _msg = (
                    f"IMPORTO FATTURA (€ {fattura.importo:,.2f}) SUPERA L'ORDINE "
                    f"(€ {(_ordine_ab.totale if _ordine_ab else 0):,.2f}) — "
                    f"Δ € {_delta:,.2f}. NON APPROVABILE."
                )
                db.session.rollback()
                flash(f'❌ APPROVAZIONE BLOCCATA — {_msg}', 'danger')
                return redirect(url_for('tecnico.approvazioni_fatture'))
        fattura.stato_approvazione = 'approvato'
        cat = 'subappalti' if fattura.sottotipo == 'subappalto' else 'materiali'
        importo_approv = fattura.importo_netto or fattura.importo or 0
        nuovo_netto_s = request.form.get('importo_netto', '').strip().replace(',', '.')
        if nuovo_netto_s:
            try:
                importo_approv = float(nuovo_netto_s)
                fattura.importo_netto = importo_approv
            except ValueError:
                pass
        desc = f'Fattura {fattura.numero or ""} – {fattura.cliente_fornitore or ""}'.strip(' –')[:300]
        voce = CostoFornitore(
            commessa_id=commessa.id, cliente_id=commessa.cliente_id,
            categoria=cat, descrizione=desc,
            importo=importo_approv,
            data=fattura.data_scadenza or date.today(),
        )
        db.session.add(voce)
        db.session.commit()
        flash(f'Fattura approvata e costo registrato in {commessa.cliente.nome} / {commessa.nome}.', 'success')
        try:
            if fattura.sottotipo == 'subappalto':
                _s, _r, _m, _o = _abbina_ordine_subappalto(fattura, commessa, current_user.id)
                if _r:
                    db.session.commit()
                    if _s == 'ok':
                        flash(f'Ordine abbinato: {_m}', 'info')
        except Exception:
            pass
        try:
            archivi_dir = os.path.join(UPLOAD_ROOT, 'archivi')
            os.makedirs(archivi_dir, exist_ok=True)
            nome_zip = (f'archivio_{commessa.codice}_{fattura.numero or fattura.id}.zip'
                        .replace(' ', '_').replace('/', '-'))
            with zf_mod.ZipFile(os.path.join(archivi_dir, nome_zip), 'w', zf_mod.ZIP_DEFLATED) as z:
                if fattura.file_path:
                    fp = os.path.join(UPLOAD_ROOT, fattura.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'fatture/{os.path.basename(fp)}')
                tecnico_obj = db.session.get(User, fattura.tecnico_id) if fattura.tecnico_id else None
                ddt_folder = f'DDT/{tecnico_obj.nome}/' if tecnico_obj else 'DDT/'
                for d in DDT.query.filter_by(fattura_id=fattura.id).all():
                    if d.file_path:
                        fp = os.path.join(UPLOAD_ROOT, d.file_path)
                        if os.path.exists(fp):
                            z.write(fp, f'{ddt_folder}{os.path.basename(fp)}')
        except Exception:
            pass
    elif azione == 'rifiuta':
        motivo = request.form.get('motivo_rifiuto', '').strip()
        fattura.stato_approvazione = 'rifiutato'
        fattura.motivo_rifiuto = motivo or None
        db.session.commit()
        commessa_nome = fattura.commessa_obj.codice if fattura.commessa_obj else '—'
        flash(
            f'{current_user.nome or current_user.email} ha rifiutato la fattura '
            f'N°{fattura.numero or "—"} inerente a commessa {commessa_nome}'
            f'{(": " + motivo) if motivo else ""}.',
            'warning'
        )
    return redirect(url_for('tecnico.approvazioni_fatture'))


@bp.route('/tecnico/fattura/<int:fattura_id>/toggle-pagamento', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def toggle_pagamento_tecnico(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))
    fattura.pagata = not fattura.pagata
    db.session.commit()
    stato = 'segnata come PAGATA' if fattura.pagata else 'riportata a NON PAGATA'
    flash(f'Fattura {stato}.', 'success')
    tab = 'subappalti' if fattura.sottotipo == 'subappalto' else 'materiali'
    if fattura.commessa_id:
        return redirect(url_for('tecnico.commessa_detail', commessa_id=fattura.commessa_id, tab=tab))
    if fattura.cliente_id:
        cliente = db.session.get(Cliente, fattura.cliente_id)
        if cliente and cliente.commesse:
            return redirect(url_for('tecnico.commessa_detail', commessa_id=cliente.commesse[0].id, tab=tab))
    return redirect(url_for('tecnico.tecnico'))


@bp.route('/tecnico/ddt')
@login_required
@ruolo_richiesto('tecnico')
def ddt_lista_tecnico():
    from blueprints.amministrazione import _ddt_ctx
    return render_template('amministrazione/ddt_lista.html', ruolo='tecnico',
                           back_url=url_for('tecnico.tecnico'),
                           anomali_url=url_for('tecnico.ddt_anomali_tecnico'),
                           ft_anomale_url=url_for('tecnico.fatture_anomale_tecnico'),
                           **_ddt_ctx(), **ctx())


@bp.route('/tecnico/ddt/anomali')
@login_required
@ruolo_richiesto('tecnico')
def ddt_anomali_tecnico():
    non_matchati    = DDT.query.filter_by(stato='non_matchato').order_by(DDT.creato_il.desc()).all()
    importo_anomalo = DDT.query.filter_by(stato='importo_anomalo').order_by(DDT.creato_il.desc()).all()
    return render_template('amministrazione/ddt_anomali.html',
                           non_matchati=non_matchati, importo_anomalo=importo_anomalo,
                           lista_url=url_for('tecnico.ddt_lista_tecnico'),
                           ft_anomale_url=url_for('tecnico.fatture_anomale_tecnico'), **ctx())


@bp.route('/tecnico/fatture-anomale')
@login_required
@ruolo_richiesto('tecnico')
def fatture_anomale_tecnico():
    fatture = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).order_by(Fattura.data_emissione.desc()).all()
    return render_template('amministrazione/fatture_anomale.html', fatture=fatture,
                           lista_url=url_for('tecnico.ddt_lista_tecnico'), **ctx())


@bp.route('/tecnico/ddt/da-approvare')
@login_required
@ruolo_richiesto('tecnico')
def ddt_da_approvare():
    if current_user.ruolo == 'Direzione':
        bolle = DDT.query.filter_by(stato='in_attesa_tecnico').order_by(DDT.creato_il.desc()).all()
    else:
        bolle = DDT.query.filter_by(
            stato='in_attesa_tecnico', tecnico_id=current_user.id
        ).order_by(DDT.creato_il.desc()).all()
    return render_template('amministrazione/ddt_da_approvare.html', bolle=bolle, **ctx())


@bp.route('/tecnico/ddt/<int:ddt_id>/approva-bolla', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def approva_bolla(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('Bolla non trovata.', 'danger')
        return redirect(url_for('tecnico.ddt_da_approvare'))
    if current_user.ruolo != 'Direzione' and ddt.tecnico_id != current_user.id:
        flash('Bolla non di tua competenza.', 'danger')
        return redirect(url_for('tecnico.ddt_da_approvare'))
    ddt.stato = 'approvato_tecnico'
    ddt.motivo_rifiuto = None
    db.session.commit()
    flash(f'Bolla "{ddt.numero}" approvata. L\'amministrazione può ora abbinarla a una fattura.', 'success')
    next_url = request.form.get('next') or request.referrer or url_for('tecnico.ddt_da_approvare')
    return redirect(next_url)


@bp.route('/tecnico/ddt/<int:ddt_id>/rifiuta-bolla', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def rifiuta_bolla(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('Bolla non trovata.', 'danger')
        return redirect(url_for('tecnico.ddt_da_approvare'))
    if current_user.ruolo != 'Direzione' and ddt.tecnico_id != current_user.id:
        flash('Bolla non di tua competenza.', 'danger')
        return redirect(url_for('tecnico.ddt_da_approvare'))
    ddt.stato = 'rifiutato_tecnico'
    ddt.motivo_rifiuto = request.form.get('motivo', '').strip() or None
    db.session.commit()
    flash(f'Bolla "{ddt.numero}" rifiutata. L\'amministrazione ne verrà informata nella sezione Bolle Rifiutate.', 'info')
    next_url = request.form.get('next') or request.referrer or url_for('tecnico.ddt_da_approvare')
    return redirect(next_url)


@bp.route('/tecnico/commessa/<int:commessa_id>/crea-fattura-attiva', methods=['POST'])
@login_required
@ruolo_richiesto('tecnico')
def crea_fattura_attiva_commessa(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa or not _check_cliente_access(commessa.cliente):
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))

    def _pd(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        except ValueError:
            return None

    def _fv(v):
        try:
            return float(str(v).replace(',', '.')) if v else None
        except ValueError:
            return None

    numero = request.form.get('numero', '').strip()
    if not numero:
        contatore = Fattura.query.filter_by(tipo='attiva', commessa_id=commessa.id).count() + 1
        numero = f'FT-{commessa.codice}-{date.today().strftime("%Y")}-{contatore:03d}'
    aliquota_iva = _fv(request.form.get('aliquota_iva', '22'))
    importo_netto = _fv(request.form.get('importo_netto', '0'))
    if importo_netto and aliquota_iva:
        importo = round(importo_netto * (1 + aliquota_iva / 100), 2)
    else:
        importo = _fv(request.form.get('importo', '0')) or 0.0
    fattura = Fattura(
        tipo='attiva', numero=numero, importo=importo, importo_netto=importo_netto,
        aliquota_iva=aliquota_iva, stato_approvazione='approvato',
        cliente_fornitore=request.form.get('cliente_fornitore', commessa.cliente.nome if commessa.cliente else '').strip(),
        piva_cliente=request.form.get('piva_cliente', '').strip() or None,
        mia_piva=request.form.get('mia_piva', '').strip() or None,
        indirizzo_cliente=request.form.get('indirizzo_cliente', '').strip() or None,
        pec_cliente=request.form.get('pec_cliente', '').strip() or None,
        cup=request.form.get('cup', '').strip() or None,
        cig=request.form.get('cig', '').strip() or None,
        codice_commessa_ref=commessa.codice,
        data_emissione=_pd(request.form.get('data_emissione', '')) or date.today(),
        data_scadenza=_pd(request.form.get('data_scadenza', '')),
        note=request.form.get('note', '').strip() or None,
        cliente_id=commessa.cliente_id, commessa_id=commessa.id,
    )
    db.session.add(fattura)
    db.session.flush()
    for i2, desc2 in enumerate(request.form.getlist('riga_desc[]')):
        desc2 = desc2.strip()
        if not desc2:
            continue
        art2  = (request.form.getlist('riga_articolo[]')[i2:i2+1] or [''])[0].strip()
        qta2  = _fv((request.form.getlist('riga_qta[]')[i2:i2+1]  or [''])[0])
        um2   = (request.form.getlist('riga_um[]')[i2:i2+1]       or [''])[0].strip()
        pu2   = _fv((request.form.getlist('riga_pu[]')[i2:i2+1]   or [''])[0])
        imp2  = round(qta2 * pu2, 2) if (qta2 and pu2) else None
        db.session.add(RigaFatturaAttiva(
            fattura_id=fattura.id, articolo=art2 or None,
            descrizione=desc2[:300], quantita=qta2,
            unita_misura=um2 or None, prezzo_unitario=pu2, importo_netto=imp2,
        ))
    db.session.commit()
    flash(f'Fattura attiva {numero} creata (€ {importo:,.2f} IVA inclusa).', 'success')
    return redirect(url_for('tecnico.commessa_detail', commessa_id=commessa_id, tab='fatture_attive'))


@bp.route('/tecnico/commessa/<int:commessa_id>/fattura-attiva/<int:fattura_id>/pdf')
@login_required
def scarica_pdf_fattura_attiva(commessa_id, fattura_id):
    from config import UPLOAD_ROOT
    from models import AppConfig
    fattura  = db.session.get(Fattura, fattura_id)
    commessa = db.session.get(Commessa, commessa_id)
    if not fattura or fattura.commessa_id != commessa_id:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('tecnico.tecnico'))

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                    Paragraph, Spacer, HRFlowable)

    C_DARK   = colors.HexColor('#1e1b4b')
    C_MID    = colors.HexColor('#4338ca')
    C_LIGHT  = colors.HexColor('#e0e7ff')
    C_ACCENT = colors.HexColor('#6366f1')
    C_GREY   = colors.HexColor('#64748b')
    C_ROW    = colors.HexColor('#f5f3ff')
    C_WHITE  = colors.white
    C_BLACK  = colors.HexColor('#0f172a')

    def ps(name, fontName='Helvetica-Bold', fontSize=10, textColor=C_BLACK,
           alignment=TA_LEFT, leading=None, spaceBefore=0, spaceAfter=0):
        return ParagraphStyle(name, fontName=fontName, fontSize=fontSize,
                              textColor=textColor, alignment=alignment,
                              leading=leading or fontSize * 1.3,
                              spaceBefore=spaceBefore, spaceAfter=spaceAfter)

    sty_brand_big  = ps('BB',  'Helvetica-Bold',  33, C_WHITE, TA_LEFT)
    sty_brand_sub  = ps('BS',  'Helvetica-Bold',   9, colors.HexColor('#fbbf24'), TA_LEFT)
    sty_label      = ps('LB',  'Helvetica',         7, C_GREY,  TA_LEFT)
    sty_value      = ps('VL',  'Helvetica-Bold',    9, C_BLACK, TA_LEFT)
    sty_doc_num    = ps('ON',  'Helvetica-Bold',   13, C_DARK,  TA_LEFT)
    sty_th         = ps('TH',  'Helvetica-Bold',    8, C_WHITE, TA_CENTER)
    sty_td         = ps('TD',  'Helvetica',          8, C_BLACK, TA_LEFT)
    sty_td_r       = ps('TDR', 'Helvetica',          8, C_BLACK, TA_RIGHT)
    sty_td_bold    = ps('TDB', 'Helvetica-Bold',    8, C_BLACK, TA_RIGHT)
    sty_footer     = ps('FT',  'Helvetica',          7, C_GREY,  TA_CENTER)
    sty_note_lbl   = ps('NL',  'Helvetica-Bold',    8, C_GREY,  TA_LEFT)
    sty_note_val   = ps('NV',  'Helvetica',          8, C_BLACK, TA_LEFT)

    buf = io.BytesIO()
    margin = 1.8 * cm
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=margin + 0.5*cm)
    story = []

    logo_inner = Table(
        [[Paragraph('<font color="#818cf8">ARTE</font><font color="#ffffff">DILE</font>', sty_brand_big)],
         [Paragraph('&#9656;  Impianti ed Edilizia', sty_brand_sub)]],
        colWidths=[7.5*cm], rowHeights=[1.9*cm, 0.75*cm]
    )
    logo_inner.setStyle(TableStyle([
        ('BACKGROUND',    (0,0), (-1,-1), colors.HexColor('#0d1117')),
        ('TOPPADDING',    (0,0), (0,0),   14),
        ('BOTTOMPADDING', (0,1), (0,1),   13),
        ('LEFTPADDING',   (0,0), (-1,-1), 18),
        ('RIGHTPADDING',  (0,0), (-1,-1), 18),
        ('ROUNDEDCORNERS',[10]),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('LINEBELOW',     (0,0), (0,0),   0.8, colors.HexColor('#818cf8')),
    ]))

    ft_num_str  = fattura.numero or f'FT-{fattura.id}'
    ft_data_str = fattura.data_emissione.strftime('%d %B %Y').upper() if fattura.data_emissione else ''
    ft_scad_str = fattura.data_scadenza.strftime('%d/%m/%Y') if fattura.data_scadenza else '—'

    logo_col = Table(
        [[logo_inner], [Spacer(1, 6)],
         [Paragraph(f'FATTURA  {ft_num_str}', sty_doc_num)],
         [Paragraph(ft_data_str, ps('DT','Helvetica', 8, C_GREY))]],
        colWidths=[7*cm]
    )
    logo_col.setStyle(TableStyle([
        ('LEFTPADDING',   (0,0),(-1,-1), 0), ('RIGHTPADDING',  (0,0),(-1,-1), 0),
        ('TOPPADDING',    (0,0),(-1,-1), 0), ('BOTTOMPADDING', (0,0),(-1,-1), 2),
    ]))

    def ref_row(label, value):
        return [Paragraph(label, sty_label), Paragraph(str(value or '—'), sty_value)]

    azienda_piva = AppConfig.get('azienda_piva', fattura.mia_piva or '')
    azienda_ind  = AppConfig.get('azienda_indirizzo', '')
    ref_data = [
        ref_row('DESTINATARIO',  fattura.cliente_fornitore),
        ref_row('P.IVA CLIENTE', fattura.piva_cliente),
        ref_row('COMMESSA',      f'{commessa.codice} — {commessa.nome}' if commessa else ''),
        ref_row('SCADENZA',      ft_scad_str),
    ]
    if fattura.cup: ref_data.append(ref_row('CUP', fattura.cup))
    if fattura.cig: ref_data.append(ref_row('CIG', fattura.cig))
    if azienda_piva: ref_data.append(ref_row('P.IVA EMITTENTE', azienda_piva))
    if azienda_ind:  ref_data.append(ref_row('INDIRIZZO',        azienda_ind))

    ref_table = Table(ref_data, colWidths=[2.8*cm, 6.6*cm])
    ref_table.setStyle(TableStyle([
        ('VALIGN',        (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING',    (0,0), (-1,-1), 3), ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('LEFTPADDING',   (0,0), (-1,-1), 0), ('RIGHTPADDING',  (0,0), (-1,-1), 0),
        ('LINEBELOW',     (0,0), (-1,-2), 0.3, colors.HexColor('#e0e7ff')),
    ]))

    header = Table([[logo_col, ref_table]], colWidths=[8*cm, 9.8*cm])
    header.setStyle(TableStyle([
        ('VALIGN',        (0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',   (0,0),(-1,-1), 0), ('RIGHTPADDING',  (0,0),(-1,-1), 0),
        ('TOPPADDING',    (0,0),(-1,-1), 0), ('BOTTOMPADDING', (0,0),(-1,-1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1,12))
    story.append(HRFlowable(width='100%', thickness=2, color=C_ACCENT, spaceAfter=12))

    righe_ft = RigaFatturaAttiva.query.filter_by(fattura_id=fattura.id).all()
    col_w = [1.2*cm, 7.2*cm, 1.4*cm, 1.3*cm, 2.7*cm, 2.7*cm]
    tbl_data = [[
        Paragraph('Art.',         sty_th), Paragraph('Descrizione',  sty_th),
        Paragraph('Qtà',          sty_th), Paragraph('U.M.',         sty_th),
        Paragraph('Prezzo unit.', sty_th), Paragraph('Importo netto',sty_th),
    ]]
    tot_netto = 0.0
    if righe_ft:
        for i, r in enumerate(righe_ft):
            imp = r.importo_netto or ((r.quantita or 0) * (r.prezzo_unitario or 0))
            tot_netto += imp or 0
            tbl_data.append([
                Paragraph(r.articolo or '', sty_td),
                Paragraph(r.descrizione or '', sty_td),
                Paragraph(f'{r.quantita:g}'.replace('.',',') if r.quantita else '', sty_td_r),
                Paragraph(r.unita_misura or '', sty_td),
                Paragraph(f'€ {r.prezzo_unitario:,.2f}' if r.prezzo_unitario else '', sty_td_r),
                Paragraph(f'€ {imp:,.2f}' if imp else '—', sty_td_bold),
            ])
    else:
        imp_fb = fattura.importo_netto or fattura.importo or 0
        tot_netto = imp_fb
        tbl_data.append([
            Paragraph('', sty_td), Paragraph(fattura.note or '(nessuna riga)', sty_td),
            Paragraph('', sty_td), Paragraph('', sty_td), Paragraph('', sty_td),
            Paragraph(f'€ {imp_fb:,.2f}', sty_td_bold),
        ])

    iva_perc  = fattura.aliquota_iva or 22.0
    imp_iva   = round(tot_netto * iva_perc / 100, 2)
    tot_ivato = round(tot_netto + imp_iva, 2)
    tbl_data += [
        [Paragraph('', sty_th)]*4 + [Paragraph('IMPONIBILE', ps('SUB','Helvetica-Bold',8,C_DARK,TA_RIGHT)), Paragraph(f'€ {tot_netto:,.2f}', sty_td_bold)],
        [Paragraph('', sty_th)]*4 + [Paragraph(f'IVA {iva_perc:.0f}%', ps('IVA','Helvetica',8,C_GREY,TA_RIGHT)), Paragraph(f'€ {imp_iva:,.2f}', sty_td_r)],
        [Paragraph('', sty_th)]*4 + [Paragraph('TOTALE', ps('TOT','Helvetica-Bold',10,C_DARK,TA_RIGHT)), Paragraph(f'€ {tot_ivato:,.2f}', ps('TV','Helvetica-Bold',11,C_ACCENT,TA_RIGHT))],
    ]
    n_r = len(righe_ft) if righe_ft else 1
    tbl = Table(tbl_data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,0),  C_DARK), ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
        ('ROWBACKGROUND',(0,1), (-1,n_r), [C_WHITE, C_ROW]),
        ('BACKGROUND',   (0,-1),(-1,-1), C_LIGHT), ('LINEABOVE',(0,-1),(-1,-1), 1.5, C_ACCENT),
        ('TOPPADDING',   (0,0), (-1,-1), 7), ('BOTTOMPADDING',(0,0), (-1,-1), 7),
        ('LEFTPADDING',  (0,0), (-1,-1), 6), ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('VALIGN',       (0,0), (-1,-1), 'MIDDLE'),
        ('INNERGRID',    (0,1), (-1,-4), 0.3, colors.HexColor('#e0e7ff')),
        ('BOX',          (0,0), (-1,-1), 0.5, C_MID), ('ROUNDEDCORNERS',[4]),
    ]))
    story.append(tbl)

    if fattura.note:
        story.append(Spacer(1,14))
        note_box = Table(
            [[Paragraph('NOTE', sty_note_lbl)], [Paragraph(fattura.note, sty_note_val)]],
            colWidths=[doc.width]
        )
        note_box.setStyle(TableStyle([
            ('BACKGROUND',    (0,0), (-1,-1), colors.HexColor('#faf5ff')),
            ('BOX',           (0,0), (-1,-1), 0.5, colors.HexColor('#c7d2fe')),
            ('TOPPADDING',    (0,0), (-1,-1), 8), ('BOTTOMPADDING',(0,0),(-1,-1),8),
            ('LEFTPADDING',   (0,0), (-1,-1), 10), ('RIGHTPADDING', (0,0),(-1,-1),10),
            ('ROUNDEDCORNERS',[6]),
        ]))
        story.append(note_box)

    story.append(Spacer(1,20))
    story.append(HRFlowable(width='100%', thickness=0.5, color=C_LIGHT, spaceAfter=6))
    story.append(Paragraph(
        f'ARTEDILE  ·  Documento generato il {datetime.utcnow().strftime("%d/%m/%Y %H:%M")}  ·  {ft_num_str}',
        sty_footer
    ))
    doc.build(story)
    buf.seek(0)
    safe_num = (fattura.numero or str(fattura.id)).replace('/', '-')
    return send_file(buf, mimetype='application/pdf',
                     download_name=f'Fattura_{safe_num}.pdf', as_attachment=True)
