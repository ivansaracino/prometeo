import io
import os
import zipfile as _zf
from datetime import date
from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file
from flask_login import login_required, current_user
from extensions import db
from models import DDT, Fattura, Commessa, Fornitore, AbbinatoDdtFattura, AbbinatoOrdineSubappalto
from config import UPLOAD_ROOT
from services.utils import ruolo_richiesto, ctx
from services.matching import _abbina_ddt_fattura
from services.fornitore import _fornitore_da_ddt

bp = Blueprint('match_archivi', __name__)


@bp.route('/match-archivi')
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi():
    oggi = date.today()
    fornitori = Fornitore.query.order_by(Fornitore.nome).all()
    abbinamenti_ddt = (AbbinatoDdtFattura.query
                       .order_by(AbbinatoDdtFattura.abbinato_il.desc()).all())
    abbinamenti_sub = (AbbinatoOrdineSubappalto.query
                       .order_by(AbbinatoOrdineSubappalto.abbinato_il.desc()).all())
    ddt_non_abbinati = DDT.query.filter(
        DDT.stato.notin_(['abbinato']),
        DDT.commessa_id != None
    ).order_by(DDT.creato_il.desc()).all()
    fat_mat_no_ddt = []
    for f in Fattura.query.filter_by(tipo='passiva', sottotipo='materiali').all():
        if not AbbinatoDdtFattura.query.filter_by(fattura_id=f.id).first():
            fat_mat_no_ddt.append(f)
    fat_sub_no_ord = []
    for f in Fattura.query.filter_by(tipo='passiva', sottotipo='subappalto').all():
        if not AbbinatoOrdineSubappalto.query.filter_by(fattura_id=f.id).first():
            fat_sub_no_ord.append(f)
    return render_template('amministrazione/match_archivi.html',
                           fornitori=fornitori,
                           abbinamenti_ddt=abbinamenti_ddt,
                           abbinamenti_sub=abbinamenti_sub,
                           ddt_non_abbinati=ddt_non_abbinati,
                           fat_mat_no_ddt=fat_mat_no_ddt,
                           fat_sub_no_ord=fat_sub_no_ord,
                           oggi=oggi, **ctx())


@bp.route('/match-archivi/match-ddt-manuale', methods=['POST'])
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi_ddt():
    ddt_id     = request.form.get('ddt_id', '').strip()
    fattura_id = request.form.get('fattura_id', '').strip()
    if not ddt_id or not fattura_id:
        flash('Seleziona DDT e Fattura.', 'warning')
        return redirect(url_for('match_archivi.match_archivi'))
    try:
        ddt     = db.session.get(DDT, int(ddt_id))
        fattura = db.session.get(Fattura, int(fattura_id))
    except (ValueError, TypeError):
        ddt = fattura = None
    if not ddt or not fattura:
        flash('DDT o Fattura non trovati.', 'danger')
        return redirect(url_for('match_archivi.match_archivi'))
    record, msg = _abbina_ddt_fattura(ddt, user_id=current_user.id)
    if record:
        db.session.commit()
        flash(f'Match completato: {msg}', 'success')
    else:
        exist = AbbinatoDdtFattura.query.filter_by(ddt_id=ddt.id, fattura_id=fattura.id).first()
        if not exist:
            archivio_zip = None
            try:
                commessa = db.session.get(Commessa, ddt.commessa_id) if ddt.commessa_id else None
                cod_comm = (commessa.codice if commessa else 'SENZA_COMMESSA').replace(' ', '_').replace('/', '-')
                dir_zip = os.path.join(UPLOAD_ROOT, 'archivi', cod_comm)
                os.makedirs(dir_zip, exist_ok=True)
                dir_gen = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti')
                os.makedirs(dir_gen, exist_ok=True)
                nome_zip = (f'MAN_DDT_{ddt.numero or ddt.id}_FT_{fattura.numero or fattura.id}'
                            f'_{cod_comm}.zip').replace(' ', '_').replace('/', '-')
                zip_path = os.path.join(dir_zip, nome_zip)
                with _zf.ZipFile(zip_path, 'w', _zf.ZIP_DEFLATED) as z:
                    for src_path, pref in [(ddt.file_path, 'BOLLA'), (fattura.file_path, 'FATTURA')]:
                        if src_path:
                            fp = os.path.join(UPLOAD_ROOT, src_path)
                            if os.path.exists(fp):
                                z.write(fp, f'{pref}_{os.path.basename(fp)}')
                import shutil as _shutil
                _shutil.copy2(zip_path, os.path.join(dir_gen, nome_zip))
                archivio_zip = os.path.join('archivi', cod_comm, nome_zip)
            except Exception:
                pass
            fornitore_match, _ = _fornitore_da_ddt(ddt)
            record = AbbinatoDdtFattura(
                ddt_id=ddt.id, fattura_id=fattura.id,
                commessa_id=ddt.commessa_id,
                fornitore_id=fornitore_match.id if fornitore_match else None,
                score_desc=1.0, archivio_zip=archivio_zip,
                abbinato_da=current_user.id,
            )
            db.session.add(record)
            ddt.fattura_id = fattura.id
            ddt.stato = 'abbinato'
            db.session.commit()
            flash(f'Match manuale creato: DDT {ddt.numero} ↔ Fattura {fattura.numero or fattura.id}', 'success')
        else:
            flash('Abbinamento già presente.', 'info')
    return redirect(url_for('match_archivi.match_archivi'))


@bp.route('/match-archivi/scarica-tutti')
@login_required
@ruolo_richiesto('match_archivi')
def match_archivi_scarica_tutti():
    records = AbbinatoDdtFattura.query.filter(
        AbbinatoDdtFattura.archivio_zip != None
    ).all()
    records_sub = AbbinatoOrdineSubappalto.query.filter(
        AbbinatoOrdineSubappalto.archivio_zip != None
    ).all()
    buf = io.BytesIO()
    added = 0
    with _zf.ZipFile(buf, 'w', _zf.ZIP_DEFLATED) as z:
        for ab in records:
            if ab.archivio_zip:
                fp = os.path.join(UPLOAD_ROOT, ab.archivio_zip)
                if os.path.exists(fp):
                    comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                    z.write(fp, f'DDT_Fatture/{comm}/{os.path.basename(fp)}')
                    added += 1
                else:
                    fp2 = os.path.join(UPLOAD_ROOT, 'archivi', 'tutti', os.path.basename(ab.archivio_zip))
                    if os.path.exists(fp2):
                        comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                        z.write(fp2, f'DDT_Fatture/{comm}/{os.path.basename(fp2)}')
                        added += 1
        for ab in records_sub:
            if ab.archivio_zip:
                fp = os.path.join(UPLOAD_ROOT, ab.archivio_zip)
                if os.path.exists(fp):
                    comm = ab.commessa.codice if ab.commessa else 'SENZA_COMMESSA'
                    z.write(fp, f'Ordini_Subappalti/{comm}/{os.path.basename(fp)}')
                    added += 1
    if added == 0:
        flash('Nessun archivio ZIP disponibile da scaricare.', 'warning')
        return redirect(url_for('match_archivi.match_archivi'))
    buf.seek(0)
    nome = f'Archivio_Completo_Abbinamenti_{date.today().strftime("%Y%m%d")}.zip'
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=nome)
