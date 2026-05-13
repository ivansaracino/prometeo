import os
import re
import zipfile
import shutil
from datetime import date
from extensions import db
from models import (Cliente, Commessa, Ordine, Fattura,
                    AbbinatoDdtFattura, AbbinatoOrdineSubappalto, User)
from services.fornitore import _sim_descrizione, _norm_piva, _fornitore_da_ddt
from config import UPLOAD_ROOT, SOGLIA_APPROVAZIONE


def _tecnico_per_commessa(commessa):
    if not commessa or not commessa.cliente:
        return None
    owner_nome = commessa.cliente.owner
    if not owner_nome:
        return None
    return User.query.filter(
        User.nome == owner_nome,
        User.ruolo.in_(['Tecnico', 'Direzione'])
    ).first()


def _estrai_indirizzo_cantiere(testo: str) -> str:
    patterns = [
        r'(?:cantiere|c/o)[:\s]+([^\n]{5,120})',
        r'(?:luogo\s+di\s+consegna|luogo\s+consegna)[:\s]+([^\n]{5,120})',
        r'(?:indirizzo\s+cantiere|destino\s+merce)[:\s]+([^\n]{5,120})',
        r'(?:consegnare\s+(?:presso|a)|spedire\s+a)[:\s]+([^\n]{5,120})',
    ]
    for p in patterns:
        m = re.search(p, testo, re.IGNORECASE)
        if m:
            return m.group(1).strip()[:300]
    return ''


def _commessa_per_indirizzo(indirizzo: str):
    if not indirizzo or len(indirizzo) < 5:
        return None, 0
    parole = [p for p in re.split(r'[\s,;.]+', indirizzo.upper()) if len(p) > 3]
    if not parole:
        return None, 0
    scores = {}
    for commessa in Commessa.query.all():
        testo_comm = ' '.join(filter(None, [commessa.via or '', commessa.luogo or ''])).upper()
        if testo_comm:
            s = sum(1 for p in parole if p in testo_comm)
            if s > 0:
                scores[commessa] = s
    for ordine in Ordine.query.filter(Ordine.commessa_id.isnot(None)).all():
        campi = ' '.join(filter(None, [
            ordine.indirizzo or '', ordine.dati_cantiere or '', ordine.rif_cantiere or ''
        ])).upper()
        if campi:
            s = sum(1 for p in parole if p in campi)
            if s > 0 and ordine.commessa_id:
                c = db.session.get(Commessa, ordine.commessa_id)
                if c:
                    scores[c] = scores.get(c, 0) + s
    if not scores:
        return None, 0
    best = max(scores, key=scores.get)
    return best, scores[best]


def _cerca_commessa_nel_testo_pdf(testo: str):
    if not testo:
        return None, '', '', ''

    cod_cl, cod_cm, metodo = '', '', ''

    m = re.search(r'\bC0*(\d{1,4})[-/](\d{1,4})\b', testo, re.IGNORECASE)
    if m:
        cod_cl, cod_cm, metodo = m.group(1), m.group(2), 'codice completo (C{cl}-{cm})'

    if not (cod_cl and cod_cm):
        m = re.search(
            r'(?:rif(?:erimento)?\.?\s*(?:cantiere|commessa|lavori|progetto)?'
            r'|vs\.?\s*rif\.?|n[°º°]?\s*commessa|commessa\s*n[°º°]?'
            r'|cod(?:ice)?\s*commessa|progetto)[:\s.#]*'
            r'C?0*(\d{1,4})[-/](\d{1,4})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cl, cod_cm = m.group(1), m.group(2)
            metodo = 'riferimento commessa (etichetta + codice)'

    if not cod_cl:
        m = re.search(
            r'(?:cli(?:ente)?|cod(?:ice)?\s*cli(?:ente)?)[:\s]+C?0*(\d{1,5})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cl = m.group(1)
    if not cod_cm:
        m = re.search(
            r'(?:com(?:messa)?|cod(?:ice)?\s*com(?:messa)?'
            r'|n[°º]?\s*com(?:messa)?|n[°º]?\s*lavoro)[:\s]+0*(\d{1,5})',
            testo, re.IGNORECASE
        )
        if m:
            cod_cm = m.group(1)
    if cod_cl and cod_cm and not metodo:
        metodo = 'codici separati (cliente + commessa)'

    if not (cod_cl and cod_cm):
        return None, cod_cl, cod_cm, metodo
    try:
        num_cl = int(cod_cl)
        num_cm = int(cod_cm)
        cli = Cliente.query.filter_by(numero=num_cl).first()
        if cli:
            commessa = Commessa.query.filter_by(cliente_id=cli.id, numero=num_cm).first()
            if commessa:
                return commessa, cod_cl, cod_cm, metodo
    except (ValueError, TypeError):
        pass

    return None, cod_cl, cod_cm, metodo


def _match_ddt(codice_cliente_raw: str, codice_commessa_raw: str, importo: float):
    raw_cl = (codice_cliente_raw or '').strip().upper().lstrip('C').lstrip('0') or '0'
    try:
        num_cl = int(raw_cl)
    except ValueError:
        return None, None, 'non_matchato', f'Codice cliente non valido: "{codice_cliente_raw}"'

    cliente = Cliente.query.filter_by(numero=num_cl).first()
    if not cliente:
        return None, None, 'non_matchato', f'Nessun cliente con codice C{num_cl:03d}'

    try:
        num_comm = int((codice_commessa_raw or '').strip())
    except ValueError:
        return None, None, 'non_matchato', f'Codice commessa non valido: "{codice_commessa_raw}"'

    commessa = Commessa.query.filter_by(cliente_id=cliente.id, numero=num_comm).first()
    if not commessa:
        return None, None, 'non_matchato', \
            f'Nessuna commessa N°{num_comm} per {cliente.nome} (C{num_cl:03d})'

    fatture_mat = Fattura.query.filter_by(
        tipo='passiva', sottotipo='materiali', commessa_id=commessa.id
    ).all()

    if not fatture_mat:
        return commessa, None, 'non_matchato', \
            f'Nessuna fattura materiali per commessa N°{num_comm} di {cliente.nome}'

    tot_fatture = sum(f.importo or 0 for f in fatture_mat)
    fattura_ref = max(fatture_mat, key=lambda f: f.importo or 0)

    if importo > tot_fatture:
        return commessa, fattura_ref, 'importo_anomalo', \
            f'DDT € {importo:,.2f} > fatture materiali € {tot_fatture:,.2f} (Δ € {importo - tot_fatture:,.2f})'

    return commessa, fattura_ref, 'abbinato', None


def _abbina_ddt_fattura(ddt, user_id=None):
    if not ddt.commessa_id:
        return None, 'DDT senza commessa assegnata: matching impossibile.'

    fornitore, piva_norm = _fornitore_da_ddt(ddt)

    candidati = Fattura.query.filter(
        Fattura.tipo == 'passiva',
        Fattura.commessa_id == ddt.commessa_id,
    ).all()

    if not candidati:
        fat_tutte_passive = Fattura.query.filter(Fattura.tipo == 'passiva').all()
        if piva_norm:
            candidati = [f for f in fat_tutte_passive
                         if f.partita_iva_fornitore
                         and _norm_piva(f.partita_iva_fornitore) == piva_norm
                         and f.commessa_id in (None, ddt.commessa_id)]
        if not candidati and fornitore:
            nome_fo = (fornitore.nome or '').strip().lower()
            candidati = [f for f in fat_tutte_passive
                         if f.cliente_fornitore
                         and _sim_descrizione(f.cliente_fornitore.lower(), nome_fo) > 0.35
                         and f.commessa_id in (None, ddt.commessa_id)]
        if not candidati:
            return None, 'Nessuna fattura passiva trovata per questa commessa o P.IVA fornitore.'

    if piva_norm:
        per_piva = [f for f in candidati
                    if f.partita_iva_fornitore and _norm_piva(f.partita_iva_fornitore) == piva_norm]
        if per_piva:
            candidati = per_piva
    elif fornitore:
        nome_fo = (fornitore.nome or '').strip().lower()
        per_nome = [f for f in candidati
                    if f.cliente_fornitore and
                    _sim_descrizione(f.cliente_fornitore.lower(), nome_fo) > 0.4]
        if per_nome:
            candidati = per_nome

    desc_ddt = ' '.join(filter(None, [ddt.descrizione, ddt.fornitore]))

    def _score_fat(ft):
        testo_ft = ' '.join(filter(None, [ft.note, ft.cliente_fornitore, ft.numero]))
        return _sim_descrizione(desc_ddt, testo_ft) if (desc_ddt and testo_ft) else 0.0

    candidati_scored = sorted(candidati, key=_score_fat, reverse=True)
    best = candidati_scored[0]
    score = _score_fat(best)

    exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=best.id).first()
    if exist:
        return exist, 'Abbinamento già presente.'

    if best.commessa_id is None and ddt.commessa_id:
        best.commessa_id = ddt.commessa_id

    archivio_zip = None
    try:
        commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
        cod_comm = (commessa.codice if commessa else 'SENZA_COMMESSA').replace(' ', '_').replace('/', '-')
        dir_zip = os.path.join(UPLOAD_ROOT, 'archivi', cod_comm)
        os.makedirs(dir_zip, exist_ok=True)
        dir_gen = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti')
        os.makedirs(dir_gen, exist_ok=True)
        nome_zip = (f'DDT_{ddt.numero or ddt.id}_FT_{best.numero or best.id}'
                    f'_{cod_comm}.zip').replace(' ', '_').replace('/', '-')
        zip_path = os.path.join(dir_zip, nome_zip)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
            for _src, prefix in [(ddt.file_path, 'BOLLA'), (best.file_path, 'FATTURA')]:
                if _src:
                    fp = os.path.join(UPLOAD_ROOT, _src)
                    if os.path.exists(fp):
                        z.write(fp, f'{prefix}_{os.path.basename(fp)}')
        shutil.copy2(zip_path, os.path.join(dir_gen, nome_zip))
        archivio_zip = os.path.join('archivi', cod_comm, nome_zip)
    except Exception:
        pass

    record = AbbinatoDdtFattura(
        ddt_id=ddt.id, fattura_id=best.id,
        commessa_id=ddt.commessa_id,
        fornitore_id=fornitore.id if fornitore else None,
        score_desc=round(score, 3),
        archivio_zip=archivio_zip, abbinato_da=user_id,
    )
    db.session.add(record)
    ddt.fattura_id = best.id
    ddt.stato = 'abbinato'
    return record, f'DDT abbinato a Fattura {best.numero or best.id} (score {score:.0%}).'


def _abbina_ordine_subappalto(fattura, commessa, user_id=None, solo_check=False):
    if not commessa:
        return 'nessun_ordine', None, 'Nessuna commessa associata.', None

    ordini = Ordine.query.filter_by(commessa_id=commessa.id).all()
    if not ordini:
        return 'nessun_ordine', None, f'Nessun ordine per {commessa.codice}.', None

    desc_ft = (fattura.note or '') + ' ' + (fattura.cliente_fornitore or '') + ' ' + (fattura.numero or '')

    def _desc_ord(o):
        return (' '.join(r.descrizione or '' for r in o.righe) + ' '
                + (o.note or '') + ' ' + (o.dati_cantiere or '') + ' ' + (o.azienda or ''))

    scored = sorted(ordini, key=lambda o: (-_sim_descrizione(desc_ft, _desc_ord(o)), -(o.totale or 0)))
    best = scored[0]

    delta = round((fattura.importo or 0) - (best.totale or 0), 2)
    stato = 'allarme' if delta > 0.01 else 'ok'

    if solo_check:
        return stato, None, '', best

    exist = AbbinatoOrdineSubappalto.query.filter_by(ordine_id=best.id, fattura_id=fattura.id).first()
    if exist:
        return exist.stato, exist, 'Abbinamento già presente.', best

    archivio_zip = None
    try:
        dir_zip = os.path.join(UPLOAD_ROOT, 'archivi', 'ordini_subappalti')
        os.makedirs(dir_zip, exist_ok=True)
        nome_zip = (f'ORD_{best.identificativo or best.id}'
                    f'_FT_{fattura.numero or fattura.id}_{commessa.codice}.zip'
                    ).replace(' ', '_').replace('/', '-')
        with zipfile.ZipFile(os.path.join(dir_zip, nome_zip), 'w', zipfile.ZIP_DEFLATED) as z:
            pdf_ord = os.path.join(UPLOAD_ROOT, 'ordini', f'ordine_{best.id}.pdf')
            if os.path.exists(pdf_ord):
                z.write(pdf_ord, f'ORDINE_{best.identificativo or best.id}.pdf')
            if fattura.file_path:
                fp = os.path.join(UPLOAD_ROOT, fattura.file_path)
                if os.path.exists(fp):
                    z.write(fp, f'FATTURA_SUBAPPALTO_{fattura.numero or fattura.id}_{os.path.basename(fp)}')
        archivio_zip = os.path.join('archivi', 'ordini_subappalti', nome_zip)
    except Exception:
        pass

    record = AbbinatoOrdineSubappalto(
        ordine_id=best.id, fattura_id=fattura.id,
        commessa_id=commessa.id, stato=stato,
        delta_importo=delta,
        score_desc=round(_sim_descrizione(desc_ft, _desc_ord(best)), 3),
        archivio_zip=archivio_zip, abbinato_da=user_id,
    )
    db.session.add(record)
    extra = (f' IMPORTO FATTURA (€ {fattura.importo:,.2f}) > ORDINE (€ {best.totale:,.2f}) — ALLARME'
             if stato == 'allarme' else '')
    return stato, record, f'Abbinato a ordine {best.identificativo or best.id} (score {_sim_descrizione(desc_ft, _desc_ord(best)):.0%}).{extra}', best


def get_soglia_corrente():
    from models import NodoOrganico
    try:
        nodo = NodoOrganico.query.filter(
            NodoOrganico.soglia_approvazione.isnot(None)
        ).order_by(NodoOrganico.soglia_approvazione.asc()).first()
        return nodo.soglia_approvazione if nodo else SOGLIA_APPROVAZIONE
    except Exception:
        return SOGLIA_APPROVAZIONE


def _scadenza_bucket(data_scad):
    if not data_scad:
        return None
    oggi = date.today()
    delta = (data_scad - oggi).days
    if delta <= 0:
        return 'oggi'
    elif delta <= 5:
        return '5gg'
    elif delta <= 10:
        return '10gg'
    elif delta <= 20:
        return '20gg'
    elif delta <= 30:
        return '30gg'
    elif delta <= 60:
        return '60gg'
    elif delta <= 90:
        return '90gg'
    elif delta <= 120:
        return '120gg'
    else:
        return '150gg'
