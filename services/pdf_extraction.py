import re
import io
from datetime import datetime, timedelta
from config import PDF_SUPPORT


def _parse_importo(raw: str):
    raw = raw.strip().replace(' ', '').replace(' ', '')
    raw = raw.lstrip('€').lstrip('EUR').strip()
    if not raw:
        return None
    if ',' in raw and '.' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    elif ',' in raw:
        raw = raw.replace(',', '.')
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_data_it(s: str):
    s = s.strip()
    s_norm = re.sub(r'[.\-]', '/', s)
    parts = s_norm.split('/')
    if len(parts) != 3:
        return None
    g, me, a = parts[0].zfill(2), parts[1].zfill(2), parts[2]
    if len(a) == 2:
        a = '20' + a
    if len(a) != 4:
        return None
    try:
        return datetime.strptime(f'{g}/{me}/{a}', '%d/%m/%Y')
    except ValueError:
        return None


def estrai_dati_fattura(testo: str) -> dict:
    result = {}
    if not testo or not testo.strip():
        return result

    linee = [l.strip() for l in testo.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    linee_nn = [l for l in linee if l]

    for pat in [
        r'(?:fattura(?:\s+(?:n\.?|nr\.?|num\.?|elettronica))?|fatt\.?|ft\.?)\s*[:\s#n°]*([A-Z0-9][A-Z0-9/\-]{0,25})',
        r'\bN[r\.]?\.?\s*[:\s]*(\d{1,6}[A-Z0-9/\-]{0,10})\b',
        r'documento\s*(?:n\.?|nr\.?)?\s*[:\s]*([A-Z0-9/\-]+)',
    ]:
        m = re.search(pat, testo, re.IGNORECASE)
        if m:
            v = m.group(1).strip().rstrip('.-').strip()
            if v and len(v) <= 20:
                result['numero'] = v
                break

    _tutti_grezzi = re.findall(r'\b(\d[\d.]*,\d{1,2})\b|(?:€|EUR)\s*(\d[\d.,]+)', testo)
    _candidati_tutti = []
    for _g in _tutti_grezzi:
        _raw = _g[0] or _g[1]
        _v = _parse_importo(_raw)
        if _v is not None and _v >= 1:
            _candidati_tutti.append(_v)
    all_candidati = sorted(set(_candidati_tutti), reverse=True)
    if all_candidati:
        result['importo'] = all_candidati[0]
        result['importo_candidati'] = all_candidati
        if len(all_candidati) >= 2:
            result['importo_netto'] = all_candidati[1]

    DATE_RE = r'\b(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})\b'

    m_sca = re.search(
        r'(?:scadenz[ae]|pagamento\s+(?:a\s+)?entro|entro\s+il|data\s+pagamento|pay\s+by)'
        r'[:\s]*(' + DATE_RE[3:-3] + r')',
        testo, re.IGNORECASE)
    if m_sca:
        d = _parse_data_it(m_sca.group(1))
        if d:
            result['data_scadenza'] = d.strftime('%Y-%m-%d')

    m_ems = re.search(
        r'(?:data\s+(?:fattura|emissione|documento|del|emiss\.?)|emessa?\s+(?:il|in\s+data)|del\s+giorno)'
        r'[:\s]*(' + DATE_RE[3:-3] + r')',
        testo, re.IGNORECASE)
    if m_ems:
        d = _parse_data_it(m_ems.group(1))
        if d:
            result['data_emissione'] = d.strftime('%Y-%m-%d')

    all_raw_dates = re.findall(DATE_RE, testo)
    parsed_dates = []
    for ds in all_raw_dates:
        d = _parse_data_it(ds)
        if d and 2000 <= d.year <= 2099:
            parsed_dates.append(d)
    if parsed_dates:
        if 'data_emissione' not in result:
            result['data_emissione'] = parsed_dates[0].strftime('%Y-%m-%d')
        if 'data_scadenza' not in result and len(parsed_dates) > 1:
            result['data_scadenza'] = parsed_dates[-1].strftime('%Y-%m-%d')

    if 'data_scadenza' not in result and 'data_emissione' in result:
        m_gg = re.search(
            r'(?:pagamento|scadenza)\s+(?:a|di|in|entro)\s+(\d{2,3})\s*(?:gg\.?|giorni)',
            testo, re.IGNORECASE)
        if m_gg:
            ems = datetime.strptime(result['data_emissione'], '%Y-%m-%d')
            sca = ems + timedelta(days=int(m_gg.group(1)))
            result['data_scadenza'] = sca.strftime('%Y-%m-%d')

    m_forn = re.search(
        r'(?:cedente|prestatore|mittente|emittente|fornitore|venditore|ditta\s+fornitrice)'
        r'[:\s]+([^\n]{3,100})',
        testo, re.IGNORECASE)
    if m_forn:
        result['cliente_fornitore'] = m_forn.group(1).strip()[:100]

    if 'cliente_fornitore' not in result:
        m_spett = re.search(r'\b(?:spett(?:\.?|abile)|egregio|all.attenzione)\b', testo, re.IGNORECASE)
        pos_spett = m_spett.start() if m_spett else len(testo)
        testo_prima = testo[:pos_spett]
        linee_prima = [l.strip() for l in testo_prima.split('\n') if l.strip()][:8]
        for linea in linee_prima:
            if (len(linea) >= 4
                    and not re.fullmatch(r'[\d/.\-\s€,:;]+', linea)
                    and not re.search(r'partita\s*iva|p\.?\s*iva|c\.?\s*f\.?|pag(?:ina)?\.?\s*\d', linea, re.IGNORECASE)
                    and not re.match(r'\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}', linea)):
                result['cliente_fornitore'] = linea[:100]
                break

    if 'cliente_fornitore' not in result and linee_nn:
        candid = linee_nn[0]
        if len(candid) >= 4 and not re.fullmatch(r'[\d/.\-\s€,:;]+', candid):
            result['cliente_fornitore'] = candid[:100]

    m_piva = re.search(
        r'(?:cedente|prestatore|mittente|emittente|fornitore|venditore|ditta)[^\n]{0,300}'
        r'(?:p\.?\s*i(?:va)?\.?|partita\s*iva)\s*[:\s]*([IT]?\d{11})',
        testo, re.IGNORECASE | re.DOTALL)
    if m_piva:
        result['partita_iva'] = m_piva.group(1).strip()
    else:
        tutte = re.findall(
            r'(?:p\.?\s*i(?:va)?\.?|partita\s*iva)\s*[:\s]*([IT]?\d{11})',
            testo, re.IGNORECASE)
        if tutte:
            result['partita_iva'] = tutte[0].strip()

    tl = testo.lower()
    sub_kw = ['subappalto', 'subappaltatore', 'nolo', 'noleggio', 'manodopera',
              'prestazione', 'servizi edili', 'lavori edili', 'lavorazioni edili',
              'installazione', 'montaggio']
    mat_kw = ['materiali', 'fornitura', 'prodotti', 'merci', 'articoli', 'forniture',
              'materiale da costruzione', 'componenti', 'ferro', 'calcestruzzo']
    sc = sum(1 for k in sub_kw if k in tl)
    mc = sum(1 for k in mat_kw if k in tl)
    result['sottotipo'] = 'subappalto' if sc > mc else 'materiali'

    return result


def _estrai_sezioni_commessa(testo: str) -> list:
    if not testo:
        return []

    from models import Cliente, Commessa

    COMM_PATS = [
        r'(?:commessa|cod(?:ice)?\.?\s*comm(?:essa)?\.?|rif(?:erimento)?\.?\s*comm(?:essa)?)'
        r'\s*[:\s#]*C?0*(\d{1,4})[-/]0*(\d{1,4})',
        r'C0*(\d{1,4})[-/]0*(\d{1,4})',
    ]
    hits = []
    for pat in COMM_PATS:
        for m in re.finditer(pat, testo, re.IGNORECASE):
            hits.append((m.start(), m.group(1), m.group(2)))

    if not hits:
        return []

    seen = set()
    unique_hits = []
    for pos, cl, cm in sorted(hits):
        key = (cl.lstrip('0') or '0', cm.lstrip('0') or '0')
        if key not in seen:
            seen.add(key)
            unique_hits.append((pos, cl, cm))

    if len(unique_hits) < 2:
        return []

    def _parse_n(s):
        s = str(s).strip().replace(' ', '').replace(' ', '')
        if ',' in s and '.' in s:
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:
            s = s.replace(',', '.')
        try:
            v = float(s)
            return v if v > 0 else None
        except (ValueError, TypeError):
            return None

    results = []
    for i, (pos, cl, cm) in enumerate(unique_hits):
        end = unique_hits[i + 1][0] if i + 1 < len(unique_hits) else len(testo)
        sect = testo[pos:end]

        commessa_obj = None
        try:
            n_cl = int(cl)
            n_cm = int(cm)
            _cli = Cliente.query.filter_by(numero=n_cl).first()
            if _cli:
                commessa_obj = Commessa.query.filter_by(cliente_id=_cli.id, numero=n_cm).first()
        except (ValueError, TypeError):
            pass

        righe = []
        totale = 0.0
        linee = [l.strip() for l in sect.split('\n') if l.strip()]
        for linea in linee:
            if re.match(r'^C?\d{1,4}[-/]\d{1,4}$', linea, re.IGNORECASE):
                continue
            nums = re.findall(r'\b\d[\d.]*,\d{1,2}\b', linea)
            if not nums:
                continue
            parsed = [v for v in (_parse_n(n) for n in nums) if v is not None]
            if not parsed:
                continue
            desc = re.sub(r'[\d.,]+', '', linea).strip()
            desc = re.sub(r'\s+', ' ', desc)[:200]
            if len(parsed) == 1:
                imp = parsed[0]
                righe.append({'descrizione': desc, 'quantita': None,
                              'prezzo_unitario': None, 'importo': imp})
                totale += imp
            else:
                ult = parsed[-1]
                qta = parsed[0]
                pu = parsed[1] if len(parsed) >= 3 else parsed[-1]
                if qta > 0 and pu > 0 and abs(qta * pu - ult) < max(ult * 0.05, 0.05):
                    righe.append({'descrizione': desc, 'quantita': qta,
                                  'prezzo_unitario': pu, 'importo': round(qta * pu, 2)})
                    totale += round(qta * pu, 2)
                elif len(parsed) == 2:
                    imp = round(parsed[0] * parsed[1], 2)
                    righe.append({'descrizione': desc, 'quantita': parsed[0],
                                  'prezzo_unitario': parsed[1], 'importo': imp})
                    totale += imp
                else:
                    righe.append({'descrizione': desc, 'quantita': None,
                                  'prezzo_unitario': None, 'importo': ult})
                    totale += ult

        codice_raw = f"C{cl}-{cm}"
        results.append({
            'codice_raw': codice_raw,
            'commessa_id': commessa_obj.id if commessa_obj else None,
            'commessa_nome': (f"{commessa_obj.codice} — {commessa_obj.nome}"
                              if commessa_obj else codice_raw),
            'cliente_nome': (commessa_obj.cliente.nome
                             if commessa_obj and commessa_obj.cliente else ''),
            'righe': righe,
            'totale': round(totale, 2),
        })

    return results


def estrai_righe_fattura(pdf_bytes):
    righe = []
    if not PDF_SUPPORT:
        return righe

    import pdfplumber

    HEADER_DESC = ['descrizione', 'lavori', 'articoli', 'prestaz', 'oggetto', 'causale', 'servizi']
    HEADER_QTA  = ['quant', 'qta', 'q.t', 'q/t', 'pezzi', 'num']
    HEADER_PU   = ['unit', 'p.u', 'p/u', 'prezzo u', 'costo u', 'listino', 'prezzo']
    HEADER_IMP  = ['import', 'totale', 'prezzo', 'tot.', 'amount', 'valore']

    def _num(s):
        if not s:
            return None
        s = str(s).strip().replace(' ', '').replace('.', '').replace(',', '.')
        try:
            return float(s)
        except ValueError:
            return None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    header_row = [str(c or '').lower().strip() for c in table[0]]

                    def _find(kws):
                        return next((i for i, h in enumerate(header_row)
                                     if any(k in h for k in kws)), None)

                    idx_d = _find(HEADER_DESC)
                    idx_q = _find(HEADER_QTA)
                    idx_u = _find(HEADER_PU)
                    idx_i = _find(HEADER_IMP)

                    if idx_d is None and idx_i is None:
                        continue

                    for row in table[1:]:
                        if not row or all(not c for c in row):
                            continue

                        def cel(i):
                            if i is None or i >= len(row):
                                return ''
                            return str(row[i] or '').strip()

                        desc = cel(idx_d)
                        if not desc or len(desc) < 2:
                            continue
                        if any(k in desc.lower() for k in HEADER_DESC):
                            continue

                        qta = _num(cel(idx_q))
                        pu = _num(cel(idx_u))
                        imp = _num(cel(idx_i))

                        if imp is None and qta is not None and pu is not None:
                            imp = round(qta * pu, 2)

                        righe.append({
                            'descrizione': desc[:300],
                            'quantita': qta,
                            'prezzo_unitario': pu,
                            'importo': imp,
                        })
    except Exception:
        pass

    return righe
