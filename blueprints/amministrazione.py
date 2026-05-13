import io
import os
import json
import zipfile
from datetime import datetime, date
from collections import Counter as _Counter
from flask import (Blueprint, render_template, redirect, url_for, request,
                   flash, jsonify, send_file, send_from_directory)
from flask_login import login_required, current_user
from extensions import db
from models import (Cliente, Commessa, CostoFornitore, Fattura, RigaFatturaAttiva,
                    DDT, RigaDDT, AbbinatoDdtFattura, User, Fornitore, AppConfig)
from config import UPLOAD_ROOT, PDF_SUPPORT, OCR_SUPPORT
from services.utils import ruolo_richiesto, ctx
from services.upload import allowed_file, salva_file, elimina_file
from services.pdf_extraction import estrai_dati_fattura, estrai_righe_fattura, _estrai_sezioni_commessa
from services.matching import (_match_ddt, _abbina_ddt_fattura,
                               _cerca_commessa_nel_testo_pdf, _estrai_indirizzo_cantiere,
                               _commessa_per_indirizzo, _tecnico_per_commessa)

bp = Blueprint('amministrazione', __name__)


def _ddt_ctx():
    tutti = DDT.query.filter(DDT.stato != 'in_revisione').order_by(DDT.creato_il.desc()).all()
    n_abbinati   = sum(1 for d in tutti if d.stato == 'abbinato')
    n_anomali    = sum(1 for d in tutti if d.stato in ('non_matchato', 'importo_anomalo'))
    tot_importo  = sum(d.importo or 0 for d in tutti)
    n_revisione  = DDT.query.filter_by(stato='in_revisione').count()
    n_rifiutate  = DDT.query.filter_by(stato='rifiutato_tecnico').count()
    n_ft_anomale = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).count()
    return dict(tutti=tutti, n_abbinati=n_abbinati, n_anomali=n_anomali,
                tot_importo=tot_importo, n_ft_anomale=n_ft_anomale,
                n_revisione=n_revisione, n_rifiutate=n_rifiutate,
                oggi=datetime.utcnow().date())


def _cascade_elimina_ddt(ddt):
    for abb in AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id).all():
        fattura = db.session.get(Fattura, abb.fattura_id)
        if fattura:
            fattura.stato = 'caricata'
        db.session.delete(abb)
    for riga in RigaDDT.query.filter_by(ddt_id=ddt.id).all():
        db.session.delete(riga)
    db.session.delete(ddt)


@bp.route('/amministrazione')
@login_required
@ruolo_richiesto('amministrazione')
def amministrazione():
    fatture_attive = Fattura.query.filter_by(tipo='attiva').order_by(Fattura.data_scadenza).all()
    fatture_passive = Fattura.query.filter_by(tipo='passiva').order_by(Fattura.data_scadenza).all()
    tot_attive = sum(f.importo for f in fatture_attive)
    tot_passive = sum(f.importo for f in fatture_passive)
    tot_attive_np = sum(f.importo for f in fatture_attive if not f.pagata)
    tot_passive_np = sum(f.importo for f in fatture_passive if not f.pagata)
    oggi = date.today()
    clienti = Cliente.query.order_by(Cliente.nome).all()
    commesse_map = {}
    for cl in clienti:
        if cl.numero:
            commesse_map[cl.numero] = {c.numero: c.nome for c in cl.commesse}
    commesse_flat = [
        {'id': c.id, 'nome': f"{c.codice} — {c.nome}",
         'cliente_nome': c.cliente.nome if c.cliente else ''}
        for cl in clienti for c in cl.commesse
    ]
    return render_template('amministrazione/amministrazione.html',
                           fatture_attive=fatture_attive, fatture_passive=fatture_passive,
                           tot_attive=tot_attive, tot_passive=tot_passive,
                           tot_attive_np=tot_attive_np, tot_passive_np=tot_passive_np,
                           oggi=oggi, clienti=clienti, commesse_map=commesse_map,
                           commesse_flat=commesse_flat, **ctx())


@bp.route('/amministrazione/carica-fattura', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def carica_fattura():
    from services.matching import _abbina_ordine_subappalto
    tipo              = request.form.get('tipo', '')
    sottotipo         = request.form.get('sottotipo', '') if tipo == 'passiva' else None
    numero            = request.form.get('numero', '').strip()
    importo_str       = request.form.get('importo', '0').replace(',', '.')
    importo_netto_str = request.form.get('importo_netto', '').replace(',', '.')
    cliente_fornitore = request.form.get('cliente_fornitore', '').strip()
    data_emissione_str = request.form.get('data_emissione', '')
    data_scadenza_str  = request.form.get('data_scadenza', '')
    note              = request.form.get('note', '').strip()
    cliente_id_str    = request.form.get('cliente_id', '').strip()
    partita_iva_cl    = request.form.get('partita_iva_cliente', '').strip()
    partita_iva_forn_form = request.form.get('partita_iva_fornitore', '').strip()
    righe_json        = request.form.get('righe_json', '[]').strip()
    codice_cli_ref    = request.form.get('codice_cliente_ref', '').strip()
    codice_com_ref    = request.form.get('codice_commessa_ref', '').strip()

    if tipo not in ('attiva', 'passiva'):
        flash('Tipo fattura non valido.', 'danger')
        return redirect(url_for('amministrazione.amministrazione'))

    try:
        importo = float(importo_str)
    except ValueError:
        importo = 0.0
    try:
        importo_netto_val = float(importo_netto_str) if importo_netto_str else None
    except ValueError:
        importo_netto_val = None

    def parse_date(s):
        try:
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        except ValueError:
            return None

    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, 'fatture')

    piva_fornitore = (partita_iva_forn_form or partita_iva_cl) if tipo == 'passiva' else None

    fattura = Fattura(
        tipo=tipo, sottotipo=sottotipo, numero=numero, importo=importo,
        importo_netto=importo_netto_val, cliente_fornitore=cliente_fornitore,
        data_emissione=parse_date(data_emissione_str),
        data_scadenza=parse_date(data_scadenza_str),
        note=note, file_path=file_path,
        stato_approvazione='approvato',
        codice_cliente_ref=codice_cli_ref or None,
        codice_commessa_ref=codice_com_ref or None,
        partita_iva_fornitore=piva_fornitore or None,
    )
    db.session.add(fattura)
    db.session.flush()

    cliente_match = None
    commessa_match = None

    if tipo == 'passiva' and codice_cli_ref and codice_com_ref:
        try:
            n_cli = int(codice_cli_ref)
            n_com = int(codice_com_ref)
            cli_by_code = Cliente.query.filter_by(numero=n_cli).first()
            if cli_by_code:
                com_by_code = Commessa.query.filter_by(
                    cliente_id=cli_by_code.id, numero=n_com
                ).first()
                if com_by_code:
                    cliente_match  = cli_by_code
                    commessa_match = com_by_code
                    fattura.cliente_id   = cli_by_code.id
                    fattura.commessa_id  = com_by_code.id
                    fattura.stato_approvazione = 'pending'
        except (ValueError, TypeError):
            pass

    commessa_id_form = request.form.get('commessa_id', '').strip()
    if not commessa_match and commessa_id_form and tipo == 'passiva':
        try:
            _comm_direct = db.session.get(Commessa, int(commessa_id_form))
            if _comm_direct:
                commessa_match = _comm_direct
                if not cliente_match and _comm_direct.cliente_id:
                    cliente_match = db.session.get(Cliente, _comm_direct.cliente_id)
                fattura.commessa_id = _comm_direct.id
                if cliente_match:
                    fattura.cliente_id = cliente_match.id
        except (ValueError, TypeError):
            pass

    if not cliente_match:
        if cliente_id_str:
            try:
                cliente_match = db.session.get(Cliente, int(cliente_id_str))
            except (ValueError, TypeError):
                pass
        if not cliente_match and partita_iva_cl:
            cliente_match = Cliente.query.filter_by(partita_iva=partita_iva_cl).first()
        if cliente_match:
            fattura.cliente_id = cliente_match.id
            if commessa_id_form and not commessa_match:
                try:
                    commessa_match = Commessa.query.filter_by(
                        id=int(commessa_id_form), cliente_id=cliente_match.id
                    ).first()
                except (ValueError, TypeError):
                    pass
            if not commessa_match:
                commessa_match = Commessa.query.filter_by(
                    cliente_id=cliente_match.id
                ).order_by(Commessa.numero).first()
            if commessa_match:
                fattura.commessa_id = commessa_match.id

    multi_commessa_flag = request.form.get('multi_commessa', '') == '1'
    if tipo == 'passiva' and multi_commessa_flag:
        _splits_raw = request.form.get('commesse_splits_json', '[]')
        try:
            _splits = json.loads(_splits_raw)
        except Exception:
            _splits = []
        _data_c = parse_date(data_scadenza_str) or date.today()
        _cat    = 'subappalti' if sottotipo == 'subappalto' else 'materiali'
        for _sp in _splits:
            _cid = None
            try:
                _cid = int(_sp.get('commessa_id') or 0) or None
            except (TypeError, ValueError):
                pass
            if not _cid:
                continue
            _comm_sp = db.session.get(Commessa, _cid)
            if not _comm_sp:
                continue
            _tot_sp = float(_sp.get('totale') or 0)
            _desc_sp = (f"Fattura {numero} – {cliente_fornitore or ''} "
                        f"[{_sp.get('codice_raw','')}]").strip(' –[]')[:300]
            db.session.add(CostoFornitore(
                commessa_id=_comm_sp.id, cliente_id=_comm_sp.cliente_id,
                categoria=_cat, descrizione=_desc_sp, importo=_tot_sp, data=_data_c,
            ))
        db.session.commit()
        flash(f'Fattura multi-commessa caricata: {len(_splits)} sezioni registrate.', 'success')
        return redirect(url_for('amministrazione.amministrazione'))

    costi_creati = 0
    if tipo == 'passiva' and cliente_match and fattura.stato_approvazione != 'pending':
        try:
            righe = json.loads(righe_json) if righe_json else []
        except Exception:
            righe = []
        data_costo = parse_date(data_scadenza_str) or date.today()
        cat = 'subappalti' if sottotipo == 'subappalto' else 'materiali'
        for riga in righe:
            desc = (riga.get('descrizione') or '').strip()
            if not desc:
                continue
            imp_r = riga.get('importo') or 0.0
            try:
                imp_r = float(imp_r)
            except (TypeError, ValueError):
                imp_r = 0.0
            voce = CostoFornitore(
                commessa_id=commessa_match.id if commessa_match else None,
                cliente_id=cliente_match.id,
                categoria=cat, descrizione=desc[:300],
                quantita=riga.get('quantita'), valore_unitario=riga.get('prezzo_unitario'),
                importo=imp_r, data=data_costo,
            )
            db.session.add(voce)
            costi_creati += 1
        if costi_creati == 0 and importo > 0:
            fornitore = cliente_fornitore or cliente_match.nome
            desc_fb = f'Fattura {numero} – {fornitore}'.strip(' –')[:300]
            db.session.add(CostoFornitore(
                commessa_id=commessa_match.id if commessa_match else None,
                cliente_id=cliente_match.id,
                categoria=cat, descrizione=desc_fb,
                importo=importo, data=data_costo,
            ))
            costi_creati += 1

    db.session.commit()

    if tipo == 'passiva' and sottotipo == 'subappalto' and commessa_match \
            and fattura.stato_approvazione != 'pending':
        _stato_b3, _, _, _ord_b3 = _abbina_ordine_subappalto(
            fattura, commessa_match, current_user.id, solo_check=True
        )
        if _stato_b3 == 'allarme' and _ord_b3:
            _delta_b3 = (fattura.importo or 0) - (_ord_b3.totale or 0)
            fattura.stato_approvazione = 'rifiutato'
            fattura.motivo_rifiuto = (
                f'IMPORTO SUPERIORE ALL\'ORDINE — NON APPROVABILE. '
                f'Fattura: € {fattura.importo:,.2f} | '
                f'Ordine ({_ord_b3.identificativo or _ord_b3.id}): € {_ord_b3.totale:,.2f} | '
                f'Eccedenza: € {_delta_b3:,.2f}'
            )
            db.session.commit()
            try:
                _abbina_ordine_subappalto(fattura, commessa_match, current_user.id)
                db.session.commit()
            except Exception:
                pass
            flash(
                f'❌ FATTURA BLOCCATA: importo (€ {fattura.importo:,.2f}) superiore '
                f'all\'ordine {_ord_b3.identificativo or _ord_b3.id} '
                f'(€ {_ord_b3.totale:,.2f}). NON APPROVABILE — eccedenza € {_delta_b3:,.2f}.',
                'danger'
            )
            return redirect(url_for('amministrazione.amministrazione'))
        elif _stato_b3 == 'ok':
            try:
                _abbina_ordine_subappalto(fattura, commessa_match, current_user.id)
                db.session.commit()
            except Exception:
                pass

    if fattura.stato_approvazione == 'pending':
        flash(
            f'Fattura passiva inviata al Tecnico per approvazione '
            f'(commessa {commessa_match.nome if commessa_match else "—"}).',
            'info'
        )
    elif costi_creati:
        flash(
            f'Fattura passiva caricata e {costi_creati} voc'
            f'{"i" if costi_creati > 1 else "e"} aggiunt'
            f'{"e" if costi_creati > 1 else "a"} automaticamente nei costi del cliente.',
            'success'
        )
    else:
        flash(f'Fattura {tipo} caricata con successo.', 'success')

    return redirect(url_for('amministrazione.amministrazione'))


@bp.route('/amministrazione/fattura/<int:fattura_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def elimina_fattura(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('amministrazione.amministrazione'))
    for abb in AbbinatoDdtFattura.query.filter_by(fattura_id=fattura.id).all():
        ddt_linked = db.session.get(DDT, abb.ddt_id)
        if ddt_linked:
            ddt_linked.fattura_id = None
            ddt_linked.stato = 'non_matchato'
        db.session.delete(abb)
    RigaFatturaAttiva.query.filter_by(fattura_id=fattura.id).delete()
    elimina_file(fattura.file_path)
    db.session.delete(fattura)
    db.session.commit()
    flash('Fattura eliminata (abbinamenti DDT ripristinati).', 'info')
    return redirect(url_for('amministrazione.amministrazione'))


@bp.route('/uploads/<path:filepath>')
@login_required
def serve_upload(filepath):
    directory = os.path.join(UPLOAD_ROOT, os.path.dirname(filepath))
    filename = os.path.basename(filepath)
    return send_from_directory(directory, filename)


@bp.route('/download/<path:filename>')
@login_required
def download_upload(filename):
    directory = os.path.join(UPLOAD_ROOT, os.path.dirname(filename))
    fn = os.path.basename(filename)
    return send_from_directory(directory, fn, as_attachment=True)


@bp.route('/amministrazione/leggi-fattura', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def leggi_fattura():
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file ricevuto'}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({'error': 'File non valido'}), 400
    try:
        content = f.read()
        fname   = f.filename.lower()
        testo   = ''
        scansione = False
        righe   = []
        IMG_EXT = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tiff', '.tif')
        is_pdf  = fname.endswith('.pdf')
        is_img  = any(fname.endswith(e) for e in IMG_EXT)

        if is_pdf:
            if PDF_SUPPORT:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    for page in pdf.pages:
                        testo += (page.extract_text() or '') + '\n'
                if testo.strip():
                    righe = estrai_righe_fattura(content)
                else:
                    scansione = True
                    if OCR_SUPPORT:
                        try:
                            from PIL import Image
                            import pytesseract
                            import pdfplumber
                            with pdfplumber.open(io.BytesIO(content)) as pdf:
                                for page in pdf.pages:
                                    img = page.to_image(resolution=200).original
                                    testo += pytesseract.image_to_string(img, lang='ita+eng') + '\n'
                            if testo.strip():
                                scansione = False
                        except Exception:
                            pass
            else:
                scansione = True
        elif is_img:
            if OCR_SUPPORT:
                try:
                    from PIL import Image
                    import pytesseract
                    img = Image.open(io.BytesIO(content))
                    img = img.convert('L')
                    testo = pytesseract.image_to_string(img, lang='ita+eng', config='--psm 3 --oem 3')
                    if not testo.strip():
                        scansione = True
                except Exception:
                    scansione = True
            else:
                scansione = True
        else:
            try:
                testo = content.decode('utf-8', errors='ignore')
            except Exception:
                scansione = True

        dati = estrai_dati_fattura(testo)
        dati['scansione'] = scansione
        dati['righe']     = righe

        if dati.get('partita_iva'):
            cliente = Cliente.query.filter_by(partita_iva=dati['partita_iva']).first()
            if cliente:
                dati['cliente_match_id']   = cliente.id
                dati['cliente_match_nome'] = cliente.nome

        if dati.get('partita_iva'):
            _pn = dati['partita_iva'].strip().upper().lstrip('IT').lstrip('0')
            _forn = next(
                (fo for fo in Fornitore.query.all()
                 if fo.partita_iva and fo.partita_iva.strip().upper().lstrip('IT').lstrip('0') == _pn),
                None
            )
            if _forn:
                dati['fornitore_match_id']   = _forn.id
                dati['fornitore_match_nome'] = _forn.nome
            _ddts = [d for d in DDT.query.filter(DDT.commessa_id != None).all()
                     if d.partita_iva and d.partita_iva.strip().upper().lstrip('IT').lstrip('0') == _pn]
            _fatt_comm = []
            if not _ddts:
                _fatt_prev = Fattura.query.filter(
                    Fattura.tipo == 'passiva',
                    Fattura.partita_iva_fornitore != None,
                    Fattura.commessa_id != None
                ).all()
                _fatt_comm = [ft for ft in _fatt_prev
                              if ft.partita_iva_fornitore and
                              ft.partita_iva_fornitore.strip().upper().lstrip('IT').lstrip('0') == _pn]
            if _ddts or _fatt_comm:
                if _ddts:
                    _cc = _Counter(d.commessa_id for d in _ddts)
                else:
                    _cc = _Counter(ft.commessa_id for ft in _fatt_comm)
                _best_id = _cc.most_common(1)[0][0]
                _comm = db.session.get(Commessa, _best_id)
                if _comm:
                    dati['commessa_suggerita_id']        = _comm.id
                    dati['commessa_suggerita_nome']      = f"{_comm.codice} — {_comm.nome}"
                    dati['commessa_suggerita_cliente_id'] = _comm.cliente_id
                    dati['commessa_suggerita_cli_num']   = _comm.cliente.numero if _comm.cliente else None
                    dati['commessa_suggerita_com_num']   = _comm.numero

        if dati.get('sottotipo') != 'subappalto':
            _sezioni = _estrai_sezioni_commessa(testo)
            if len(_sezioni) >= 2:
                dati['multi_commessa'] = True
                dati['commesse_splits'] = [
                    {
                        'codice_raw':    _s['codice_raw'],
                        'commessa_id':   _s['commessa_id'],
                        'commessa_nome': _s['commessa_nome'],
                        'cliente_nome':  _s['cliente_nome'],
                        'totale':        _s['totale'],
                        'righe':         _s['righe'],
                    }
                    for _s in _sezioni
                ]

        return jsonify(dati)
    except Exception as e:
        return jsonify({'error': str(e), 'sottotipo': 'materiali', 'righe': []}), 200


@bp.route('/amministrazione/ddt')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_lista():
    return render_template('amministrazione/ddt_lista.html', ruolo='amministrativo',
                           back_url=url_for('amministrazione.amministrazione'),
                           anomali_url=url_for('amministrazione.ddt_anomali'),
                           ft_anomale_url=url_for('amministrazione.fatture_anomale'),
                           **_ddt_ctx(), **ctx())


@bp.route('/amministrazione/ddt/anomali')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_anomali():
    non_matchati    = DDT.query.filter_by(stato='non_matchato').order_by(DDT.creato_il.desc()).all()
    importo_anomalo = DDT.query.filter_by(stato='importo_anomalo').order_by(DDT.creato_il.desc()).all()
    return render_template('amministrazione/ddt_anomali.html',
                           non_matchati=non_matchati, importo_anomalo=importo_anomalo,
                           lista_url=url_for('amministrazione.ddt_lista'),
                           ft_anomale_url=url_for('amministrazione.fatture_anomale'), **ctx())


@bp.route('/amministrazione/fatture-anomale')
@login_required
@ruolo_richiesto('amministrazione')
def fatture_anomale():
    fatture = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali'
    ).filter(Fattura.commessa_id == None).order_by(Fattura.data_emissione.desc()).all()
    return render_template('amministrazione/fatture_anomale.html', fatture=fatture,
                           lista_url=url_for('amministrazione.ddt_lista'), **ctx())


@bp.route('/amministrazione/ddt/importa-gmail', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def importa_ddt_gmail():
    import imaplib
    import email as emaillib
    import email.utils as emailutils
    import uuid
    import re

    gmail_user = os.environ.get('GMAIL_USER', 'bolleddt13@gmail.com')
    gmail_pass = os.environ.get('GMAIL_APP_PASSWORD', 'wkwkybgcnnbzqlay')
    if not gmail_pass:
        flash("Password Gmail non configurata. Imposta GMAIL_APP_PASSWORD nelle variabili d'ambiente.", 'danger')
        return redirect(url_for('amministrazione.ddt_lista'))

    UID_KEY = 'gmail_ddt_uid_visti'
    uid_visti = set(json.loads(AppConfig.get(UID_KEY, '[]')))
    risultati = {'ok': 0, 'saltati': 0}

    try:
        mail = imaplib.IMAP4_SSL('imap.gmail.com', 993)
        try:
            mail.login(gmail_user, gmail_pass)
        except imaplib.IMAP4.error as login_err:
            err_msg = str(login_err)
            if any(k in err_msg for k in ('Application-specific password required', 'AUTHENTICATE', 'Invalid credentials')):
                flash(
                    'Gmail ha rifiutato la password. '
                    'Per usare IMAP devi generare una "App Password" di 16 caratteri dal tuo account Google: '
                    'Google Account → Sicurezza → Verifica in 2 passaggi → App password. '
                    f'Errore originale: {err_msg}', 'danger'
                )
            else:
                flash(f'Errore login Gmail ({gmail_user}): {err_msg}', 'danger')
            return redirect(url_for('amministrazione.ddt_lista'))
        mail.select('INBOX')
        _, search_data = mail.search(None, 'ALL')
        msg_ids = search_data[0].split()
        nuovi_uid = set()

        for num in msg_ids:
            _, uid_data = mail.fetch(num, '(UID)')
            uid_str = uid_data[0].decode() if uid_data and uid_data[0] else ''
            uid_match = re.search(r'UID\s+(\d+)', uid_str)
            uid = uid_match.group(1) if uid_match else num.decode()
            if uid in uid_visti:
                risultati['saltati'] += 1
                continue
            _, msg_data = mail.fetch(num, '(RFC822)')
            msg = emaillib.message_from_bytes(msg_data[0][1])
            mittente = emailutils.parseaddr(msg.get('From', ''))[1]

            for part in msg.walk():
                ct = part.get_content_type()
                fn = (part.get_filename() or '').strip()
                is_pdf = (ct == 'application/pdf' or fn.lower().endswith('.pdf') or
                          (ct == 'application/octet-stream' and fn.lower().endswith('.pdf')))
                if not is_pdf:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                safe_name  = f"{uuid.uuid4().hex}_{fn or 'ddt.pdf'}"
                upload_dir = os.path.join(UPLOAD_ROOT, 'ddt')
                os.makedirs(upload_dir, exist_ok=True)
                fpath = os.path.join(upload_dir, safe_name)
                with open(fpath, 'wb') as fp:
                    fp.write(payload)
                rel_path = os.path.join('ddt', safe_name)

                numero_ddt         = fn.replace('.pdf', '').replace('.PDF', '') or f'Gmail-{uid}'
                fornitore_ddt      = mittente
                importo_ddt        = 0.0
                data_ddt           = datetime.utcnow().date()
                codice_cliente     = ''
                codice_commessa    = ''
                indirizzo_cantiere = ''

                try:
                    import pdfplumber
                    with pdfplumber.open(io.BytesIO(payload)) as pdf:
                        testo = '\n'.join(p.extract_text() or '' for p in pdf.pages)
                    dati = estrai_dati_fattura(testo)
                    numero_ddt    = dati.get('numero')            or numero_ddt
                    fornitore_ddt = dati.get('cliente_fornitore') or fornitore_ddt
                    importo_ddt   = float(dati.get('importo') or 0)
                    if dati.get('data_emissione'):
                        try:
                            data_ddt = datetime.strptime(dati['data_emissione'], '%Y-%m-%d').date()
                        except ValueError:
                            pass
                    commessa_da_codice, codice_cliente, codice_commessa, _ = \
                        _cerca_commessa_nel_testo_pdf(testo)
                    indirizzo_cantiere = _estrai_indirizzo_cantiere(testo)
                except Exception:
                    commessa_da_codice = None

                ddt = DDT(
                    numero=numero_ddt, data=data_ddt,
                    fornitore=fornitore_ddt or None,
                    codice_cliente=codice_cliente, codice_commessa=codice_commessa,
                    indirizzo_cantiere=indirizzo_cantiere or None,
                    importo=importo_ddt,
                    commessa_id=commessa_da_codice.id if commessa_da_codice else None,
                    fattura_id=None, stato='in_revisione',
                    note_anomalia='Importato da Gmail — da verificare e assegnare.',
                    file_path=rel_path, creato_da=current_user.id,
                )
                db.session.add(ddt)
                risultati['ok'] += 1
            nuovi_uid.add(uid)

        mail.close()
        mail.logout()
        db.session.commit()

        tutti_uid = uid_visti | nuovi_uid
        if len(tutti_uid) > 2000:
            tutti_uid = set(sorted(tutti_uid, key=lambda x: int(x) if x.isdigit() else 0)[-2000:])
        AppConfig.set(UID_KEY, json.dumps(list(tutti_uid)))

        tot = risultati['ok']
        if tot == 0:
            flash(f'Nessun nuovo DDT trovato in {gmail_user}'
                  f'{(" ("+str(risultati["saltati"])+" già importati)") if risultati["saltati"] else ""}.', 'info')
        else:
            extra = f" ({risultati['saltati']} già presenti saltati)" if risultati['saltati'] else ''
            flash(f'{tot} DDT importati da {gmail_user}{extra}. '
                  f'Aprire la sezione "In Revisione" per verificarli e assegnarli.', 'success')
    except Exception as e:
        flash(f'Errore connessione Gmail ({gmail_user}): {e}', 'danger')
    return redirect(url_for('amministrazione.ddt_lista'))


@bp.route('/amministrazione/ddt/nuovo', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def nuovo_ddt():
    numero          = request.form.get('numero', '').strip()
    data_str        = request.form.get('data', '').strip()
    fornitore       = request.form.get('fornitore', '').strip()
    codice_cliente  = request.form.get('codice_cliente', '').strip()
    codice_commessa = request.form.get('codice_commessa', '').strip()
    descrizione     = request.form.get('descrizione', '').strip()
    try:
        importo = float(request.form.get('importo', '0').replace(',', '.'))
    except ValueError:
        importo = 0.0
    try:
        data = datetime.strptime(data_str, '%Y-%m-%d').date() if data_str else datetime.utcnow().date()
    except ValueError:
        data = datetime.utcnow().date()

    file_path = None
    if 'file' in request.files:
        f = request.files['file']
        if f and f.filename and allowed_file(f.filename):
            file_path = salva_file(f, 'ddt')

    commessa, fattura_ref, stato, note_anomalia = _match_ddt(codice_cliente, codice_commessa, importo)
    ddt = DDT(
        numero=numero, data=data, fornitore=fornitore or None,
        codice_cliente=codice_cliente, codice_commessa=codice_commessa,
        importo=importo, descrizione=descrizione or None,
        commessa_id=commessa.id if commessa else None,
        fattura_id=fattura_ref.id if fattura_ref else None,
        stato=stato, note_anomalia=note_anomalia,
        file_path=file_path, creato_da=current_user.id,
    )
    db.session.add(ddt)
    db.session.commit()

    if stato == 'abbinato':
        flash(f'DDT "{numero}" abbinato correttamente a {commessa.cliente.nome} / {commessa.nome}.', 'success')
    elif stato == 'importo_anomalo':
        flash(f'DDT "{numero}" caricato ma con IMPORTO ANOMALO: {note_anomalia}', 'warning')
    else:
        flash(f'DDT "{numero}" caricato ma NON MATCHATO: {note_anomalia}', 'danger')
    return redirect(url_for('amministrazione.ddt_lista'))


@bp.route('/amministrazione/ddt/<int:ddt_id>/elimina', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def elimina_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
    else:
        _cascade_elimina_ddt(ddt)
        db.session.commit()
        flash('DDT eliminato con tutti i dati collegati.', 'success')
    return redirect(request.referrer or url_for('amministrazione.ddt_lista'))


@bp.route('/amministrazione/ddt/revisione')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_revisione_lista():
    in_revisione = DDT.query.filter_by(stato='in_revisione').order_by(DDT.creato_il.desc()).all()
    return render_template('amministrazione/ddt_revisione.html',
                           in_revisione=in_revisione,
                           lista_url=url_for('amministrazione.ddt_lista'), **ctx())


@bp.route('/amministrazione/ddt/<int:ddt_id>/revisiona')
@login_required
@ruolo_richiesto('amministrazione')
def revisiona_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt or ddt.stato != 'in_revisione':
        flash('DDT non trovato o già processato.', 'danger')
        return redirect(url_for('amministrazione.ddt_revisione_lista'))
    clienti  = Cliente.query.order_by(Cliente.numero).all()
    commesse = Commessa.query.order_by(Commessa.numero).all()
    tecnici  = User.query.filter(
        User.ruolo.in_(['Tecnico', 'Direzione']), User.attivo == True
    ).order_by(User.nome).all()

    commessa_suggerita  = None
    tecnico_suggerito   = None
    metodo_suggerimento = ''
    score_indirizzo     = 0

    if ddt.commessa_id:
        commessa_suggerita = db.session.get(Commessa, ddt.commessa_id)
        if commessa_suggerita:
            metodo_suggerimento = 'codice completo trovato nel PDF'

    if not commessa_suggerita and ddt.codice_cliente and ddt.codice_commessa:
        raw_cl = (ddt.codice_cliente or '').strip().upper().lstrip('C').lstrip('0') or '0'
        try:
            num_cl = int(raw_cl)
            cli = Cliente.query.filter_by(numero=num_cl).first()
            if cli:
                num_com = int((ddt.codice_commessa or '').strip())
                commessa_suggerita = Commessa.query.filter_by(
                    cliente_id=cli.id, numero=num_com
                ).first()
                if commessa_suggerita:
                    metodo_suggerimento = 'codici estratti dal PDF'
        except (ValueError, TypeError):
            pass

    if not commessa_suggerita and ddt.indirizzo_cantiere:
        commessa_suggerita, score_indirizzo = _commessa_per_indirizzo(ddt.indirizzo_cantiere)
        if commessa_suggerita:
            metodo_suggerimento = f'indirizzo cantiere ({score_indirizzo} corrispondenze)'

    if commessa_suggerita:
        tecnico_suggerito = _tecnico_per_commessa(commessa_suggerita)

    sel_cliente_num  = str(commessa_suggerita.cliente.numero) \
        if commessa_suggerita and commessa_suggerita.cliente else (ddt.codice_cliente or '')
    sel_commessa_num = str(commessa_suggerita.numero) \
        if commessa_suggerita else (ddt.codice_commessa or '')

    fornitore_suggerito = None
    if ddt.partita_iva:
        piva_n = ddt.partita_iva.strip().upper().lstrip('IT').lstrip('0')
        for forn in Fornitore.query.all():
            if forn.partita_iva and forn.partita_iva.strip().upper().lstrip('IT').lstrip('0') == piva_n:
                fornitore_suggerito = forn
                break

    righe_salvate = RigaDDT.query.filter_by(ddt_id=ddt.id).all()
    righe_pdf = []
    if not righe_salvate and ddt.file_path and PDF_SUPPORT:
        fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
        if os.path.exists(fp):
            with open(fp, 'rb') as _f:
                pdf_bytes = _f.read()
            righe_pdf = estrai_righe_fattura(pdf_bytes)

    tutte_commesse = Commessa.query.order_by(Commessa.numero).all()
    return render_template('amministrazione/ddt_revisiona.html', ddt=ddt,
                           clienti=clienti, commesse=commesse, tecnici=tecnici,
                           commessa_suggerita=commessa_suggerita,
                           metodo_suggerimento=metodo_suggerimento,
                           tecnico_suggerito=tecnico_suggerito,
                           fornitore_suggerito=fornitore_suggerito,
                           score_indirizzo=score_indirizzo,
                           sel_cliente_num=sel_cliente_num,
                           sel_commessa_num=sel_commessa_num,
                           lista_url=url_for('amministrazione.ddt_revisione_lista'),
                           righe_pdf=righe_pdf,
                           righe_salvate=righe_salvate,
                           tutte_commesse=tutte_commesse, **ctx())


@bp.route('/amministrazione/ddt/<int:ddt_id>/conferma', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def conferma_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('amministrazione.ddt_revisione_lista'))

    ddt.numero          = request.form.get('numero', ddt.numero or '').strip()
    ddt.fornitore       = request.form.get('fornitore', '').strip() or ddt.fornitore
    ddt.codice_cliente  = request.form.get('codice_cliente', '').strip()
    ddt.codice_commessa = request.form.get('codice_commessa', '').strip()
    ddt.descrizione     = request.form.get('descrizione', '').strip() or None
    ddt.partita_iva     = request.form.get('partita_iva', '').strip() or ddt.partita_iva or None
    try:
        importo_str = request.form.get('importo', '').strip()
        if importo_str:
            ddt.importo = float(importo_str.replace(',', '.'))
    except ValueError:
        pass
    data_str = request.form.get('data', '').strip()
    if data_str:
        try:
            ddt.data = datetime.strptime(data_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    commessa = None
    raw_cl = (ddt.codice_cliente or '').strip().upper().lstrip('C').lstrip('0') or '0'
    try:
        num_cl = int(raw_cl)
        cli = Cliente.query.filter_by(numero=num_cl).first()
        if cli:
            num_com = int((ddt.codice_commessa or '').strip())
            commessa = Commessa.query.filter_by(cliente_id=cli.id, numero=num_com).first()
    except (ValueError, TypeError):
        pass
    if not commessa and ddt.commessa_id:
        commessa = db.session.get(Commessa, ddt.commessa_id)
    if commessa:
        ddt.commessa_id = commessa.id

    tecnico = None
    tecnico_id_str = request.form.get('tecnico_id', '').strip()
    if tecnico_id_str:
        try:
            tecnico = db.session.get(User, int(tecnico_id_str))
        except (ValueError, TypeError):
            pass
    assegnazione_auto = False
    if not tecnico and commessa:
        tecnico = _tecnico_per_commessa(commessa)
        if tecnico:
            assegnazione_auto = True

    RigaDDT.query.filter_by(ddt_id=ddt.id).delete()
    descs   = request.form.getlist('riga_desc[]')
    qtas    = request.form.getlist('riga_qta[]')
    pus     = request.form.getlist('riga_pu[]')
    umis    = request.form.getlist('riga_um[]')
    com_ids = request.form.getlist('riga_commessa_id[]')
    for idx, desc in enumerate(descs):
        desc = desc.strip()
        if not desc:
            continue
        def _f(lst, i):
            try:
                v = lst[i].strip().replace(',', '.') if i < len(lst) else ''
                return float(v) if v else None
            except ValueError:
                return None
        qta = _f(qtas, idx)
        pu  = _f(pus,  idx)
        imp = round(qta * pu, 2) if (qta is not None and pu is not None) else None
        cid_str = com_ids[idx].strip() if idx < len(com_ids) else ''
        commessa_riga = None
        if cid_str:
            try:
                commessa_riga = db.session.get(Commessa, int(cid_str))
            except (ValueError, TypeError):
                pass
        um = umis[idx].strip() if idx < len(umis) else ''
        db.session.add(RigaDDT(
            ddt_id=ddt.id,
            commessa_id=commessa_riga.id if commessa_riga else None,
            codice_commessa=commessa_riga.codice if commessa_riga else cid_str or None,
            descrizione=desc[:300],
            quantita=qta, unita_misura=um or None,
            prezzo_unitario=pu, importo=imp
        ))
    db.session.flush()

    if tecnico:
        ddt.tecnico_id = tecnico.id
        ddt.stato = 'in_attesa_tecnico'
        ddt.note_anomalia = None
        db.session.commit()
        if assegnazione_auto:
            flash(
                f'Bolla "{ddt.numero}" assegnata automaticamente a {tecnico.nome} '
                f'(responsabile commessa {commessa.codice}) — inviata per approvazione.', 'info'
            )
        else:
            flash(
                f'Bolla "{ddt.numero}" inviata a {tecnico.nome} per approvazione'
                f'{(" — Commessa: " + commessa.codice) if commessa else ""}.', 'info'
            )
    else:
        ddt.stato = 'in_revisione'
        if commessa:
            ddt.note_anomalia = (
                f'Commessa {commessa.codice} trovata ma nessun responsabile assegnato. '
                'Seleziona un tecnico manualmente.'
            )
            flash(
                f'Commessa {commessa.codice} trovata ma non ha un responsabile tecnico. '
                'Assegna un tecnico manualmente dalla revisione.', 'warning'
            )
        else:
            ddt.note_anomalia = 'Nessuna commessa trovata — assegnare commessa e tecnico manualmente.'
            flash(
                f'Bolla "{ddt.numero}": nessuna commessa identificata. '
                'Seleziona commessa e tecnico manualmente dalla revisione.', 'warning'
            )
        db.session.commit()

    try:
        _rec, _msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
        if _rec:
            db.session.commit()
            flash(f'Abbinamento automatico riuscito: {_msg}', 'success')
    except Exception:
        pass

    n_rimasti = DDT.query.filter_by(stato='in_revisione').count()
    if n_rimasti > 0:
        return redirect(url_for('amministrazione.ddt_revisione_lista'))
    flash('Tutte le bolle in revisione sono state processate.', 'success')
    return redirect(url_for('amministrazione.ddt_lista'))


@bp.route('/amministrazione/ddt/<int:ddt_id>/rifiuta', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def rifiuta_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('amministrazione.ddt_revisione_lista'))
    elimina_file(ddt.file_path)
    _cascade_elimina_ddt(ddt)
    db.session.commit()
    flash('DDT rifiutato ed eliminato.', 'info')
    return redirect(url_for('amministrazione.ddt_revisione_lista'))


@bp.route('/amministrazione/ddt/rifiutate')
@login_required
@ruolo_richiesto('amministrazione')
def ddt_rifiutate():
    rifiutate = DDT.query.filter_by(stato='rifiutato_tecnico').order_by(DDT.creato_il.desc()).all()
    ctx_tecnici = User.query.filter(
        User.ruolo.in_(['Tecnico', 'Direzione']), User.attivo == True
    ).order_by(User.nome).all()
    return render_template('amministrazione/ddt_rifiutate.html', rifiutate=rifiutate,
                           ctx_tecnici=ctx_tecnici,
                           lista_url=url_for('amministrazione.ddt_lista'), **ctx())


@bp.route('/amministrazione/fattura/<int:fattura_id>/abbina-ddt', methods=['GET', 'POST'])
@login_required
@ruolo_richiesto('amministrazione')
def abbina_ddt_fattura_view(fattura_id):
    fattura = db.session.get(Fattura, fattura_id)
    if not fattura:
        flash('Fattura non trovata.', 'danger')
        return redirect(url_for('amministrazione.amministrazione'))
    if request.method == 'POST':
        ddt_ids = request.form.getlist('ddt_ids')
        if not ddt_ids:
            flash('Seleziona almeno una bolla da abbinare.', 'warning')
            return redirect(url_for('amministrazione.abbina_ddt_fattura_view', fattura_id=fattura_id))
        tecnico_assegnato = None
        for sid in ddt_ids:
            try:
                d = db.session.get(DDT, int(sid))
                if d:
                    d.fattura_id = fattura.id
                    d.stato = 'abbinato'
                    if d.tecnico_id and not tecnico_assegnato:
                        tecnico_assegnato = db.session.get(User, d.tecnico_id)
            except (ValueError, TypeError):
                pass
        if tecnico_assegnato:
            fattura.tecnico_id = tecnico_assegnato.id
        fattura.stato_approvazione = 'pending'
        db.session.commit()
        flash(
            f'Bolle abbinate alla fattura {fattura.numero or ""}. '
            f'{"Inviata a " + tecnico_assegnato.nome + " per approvazione." if tecnico_assegnato else "Fattura inviata per approvazione."}',
            'info'
        )
        return redirect(url_for('amministrazione.amministrazione'))
    ddt_disponibili = []
    if fattura.commessa_id:
        ddt_disponibili = DDT.query.filter(
            DDT.commessa_id == fattura.commessa_id,
            DDT.stato.in_(['approvato_tecnico', 'non_matchato', 'importo_anomalo'])
        ).order_by(DDT.data.desc()).all()
    ddt_abbinati = DDT.query.filter_by(fattura_id=fattura_id).all()
    return render_template('amministrazione/fattura_abbina_ddt.html',
                           fattura=fattura, ddt_disponibili=ddt_disponibili,
                           ddt_abbinati=ddt_abbinati, **ctx())


@bp.route('/amministrazione/archivi')
@login_required
@ruolo_richiesto('amministrazione')
def archivi():
    fatture_approvate = (
        Fattura.query
        .filter_by(tipo='passiva', stato_approvazione='approvato')
        .filter(Fattura.commessa_id.isnot(None))
        .order_by(Fattura.data_emissione.desc())
        .all()
    )
    commesse_arch = {}
    for f in fatture_approvate:
        linked_ddt = DDT.query.filter_by(fattura_id=f.id).all()
        cid = f.commessa_id
        if cid not in commesse_arch:
            commesse_arch[cid] = {'commessa': f.commessa_obj, 'fatture': []}
        commesse_arch[cid]['fatture'].append({'fattura': f, 'bolle': linked_ddt})
    return render_template('amministrazione/archivi.html',
                           commesse_arch=list(commesse_arch.values()),
                           fatture_con_ddt=[], **ctx())


@bp.route('/amministrazione/archivi/commessa/<int:commessa_id>/scarica')
@login_required
@ruolo_richiesto('amministrazione')
def scarica_archivio_commessa(commessa_id):
    commessa = db.session.get(Commessa, commessa_id)
    if not commessa:
        flash('Commessa non trovata.', 'danger')
        return redirect(url_for('amministrazione.archivi'))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        fatture = Fattura.query.filter_by(
            commessa_id=commessa_id, tipo='passiva', stato_approvazione='approvato'
        ).all()
        for fat in fatture:
            if fat.file_path:
                fp = os.path.join(UPLOAD_ROOT, fat.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'fatture/{os.path.basename(fp)}')
            for ddt in DDT.query.filter_by(fattura_id=fat.id).all():
                tecnico_obj = db.session.get(User, ddt.tecnico_id) if ddt.tecnico_id else None
                sub = f'DDT/{tecnico_obj.nome}/' if tecnico_obj else 'DDT/'
                if ddt.file_path:
                    fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'{sub}{os.path.basename(fp)}')
    buf.seek(0)
    nome = f'archivio_{commessa.codice}_{commessa.nome[:20]}.zip'.replace(' ', '_')
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=nome)


@bp.route('/amministrazione/archivi/generale')
@login_required
@ruolo_richiesto('amministrazione')
def scarica_archivio_generale():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for fat in Fattura.query.filter_by(tipo='passiva', stato_approvazione='approvato').all():
            commessa = db.session.get(Commessa, fat.commessa_id) if fat.commessa_id else None
            folder = commessa.codice.replace('/', '-') if commessa else 'senza_commessa'
            if fat.file_path:
                fp = os.path.join(UPLOAD_ROOT, fat.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'{folder}/fatture/{os.path.basename(fp)}')
            for ddt in DDT.query.filter_by(fattura_id=fat.id).all():
                tecnico_obj = db.session.get(User, ddt.tecnico_id) if ddt.tecnico_id else None
                sub = f'{folder}/DDT/{tecnico_obj.nome}/' if tecnico_obj else f'{folder}/DDT/'
                if ddt.file_path:
                    fp = os.path.join(UPLOAD_ROOT, ddt.file_path)
                    if os.path.exists(fp):
                        z.write(fp, f'{sub}{os.path.basename(fp)}')
    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name='archivio_generale_fatture_ddt.zip')


@bp.route('/amministrazione/abbinamenti/ddt-fatture')
@login_required
@ruolo_richiesto('amministrazione')
def archivio_abbinamenti_ddt():
    records = (AbbinatoDdtFattura.query
               .order_by(AbbinatoDdtFattura.abbinato_il.desc()).all())
    return render_template('amministrazione/archivio_abbinamenti_ddt.html', records=records, **ctx())


@bp.route('/amministrazione/abbinamenti/ddt-fatture/riesegui/<int:ddt_id>', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def riesegui_abbinamento_ddt(ddt_id):
    ddt = db.session.get(DDT, ddt_id)
    if not ddt:
        flash('DDT non trovato.', 'danger')
        return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))
    record, msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
    if record:
        db.session.commit()
    flash(msg, 'success' if record else 'warning')
    return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))


@bp.route('/amministrazione/abbinamenti/ddt-fatture/abbina-manuale', methods=['POST'])
@login_required
@ruolo_richiesto('amministrazione')
def abbina_ddt_fattura_manuale():
    from services.fornitore import _fornitore_da_ddt
    ddt_id     = request.form.get('ddt_id', '').strip()
    fattura_id = request.form.get('fattura_id', '').strip()
    if not ddt_id or not fattura_id:
        flash('Seleziona sia il DDT che la fattura.', 'warning')
        return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))
    try:
        ddt     = db.session.get(DDT, int(ddt_id))
        fattura = db.session.get(Fattura, int(fattura_id))
    except (ValueError, TypeError):
        ddt = fattura = None
    if not ddt or not fattura:
        flash('DDT o Fattura non trovati.', 'danger')
        return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))
    exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=fattura.id).first()
    if exist:
        flash('Abbinamento già presente.', 'info')
        return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))
    archivio_zip = None
    try:
        commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
        dir_zip  = os.path.join(UPLOAD_ROOT, 'archivi', 'ddt_fatture')
        os.makedirs(dir_zip, exist_ok=True)
        nome_zip = (f'DDT_{ddt.numero or ddt.id}_FT_{fattura.numero or fattura.id}'
                    f'_{commessa.codice if commessa else "X"}_MAN.zip'
                    ).replace(' ', '_').replace('/', '-')
        with zipfile.ZipFile(os.path.join(dir_zip, nome_zip), 'w', zipfile.ZIP_DEFLATED) as z:
            for src, pref in [(ddt.file_path, 'BOLLA'), (fattura.file_path, 'FATTURA')]:
                if src:
                    fp = os.path.join(UPLOAD_ROOT, src)
                    if os.path.exists(fp):
                        z.write(fp, f'{pref}_{os.path.basename(fp)}')
        archivio_zip = os.path.join('archivi', 'ddt_fatture', nome_zip)
    except Exception:
        pass
    fornitore_man, _ = _fornitore_da_ddt(ddt)
    record = AbbinatoDdtFattura(
        ddt_id=ddt.id, fattura_id=fattura.id,
        commessa_id=ddt.commessa_id,
        fornitore_id=fornitore_man.id if fornitore_man else None,
        score_desc=1.0, archivio_zip=archivio_zip,
        abbinato_da=current_user.id,
    )
    db.session.add(record)
    ddt.fattura_id = fattura.id
    db.session.commit()
    flash(f'DDT {ddt.numero} abbinato manualmente a Fattura {fattura.numero or fattura.id}.', 'success')
    return redirect(url_for('amministrazione.archivio_abbinamenti_ddt'))


@bp.route('/amministrazione/abbinamenti/ordini-subappalti')
@login_required
@ruolo_richiesto('amministrazione')
def archivio_abbinamenti_subappalti():
    from models import AbbinatoOrdineSubappalto
    records = (AbbinatoOrdineSubappalto.query
               .order_by(AbbinatoOrdineSubappalto.abbinato_il.desc()).all())
    return render_template('amministrazione/archivio_abbinamenti_subappalti.html', records=records, **ctx())
