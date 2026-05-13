import re
from models import Fornitore, Fattura


def _sim_descrizione(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    stop = {'di', 'il', 'la', 'le', 'lo', 'gli', 'i', 'e', 'in', 'su', 'per', 'a', 'da',
            'con', 'non', 'del', 'della', 'dei', 'degli', 'delle', 'un', 'una', 'uno',
            'che', 'si', 'al', 'ai', 'alle', 'agli', 'è', 'ha', 'nei', 'nel', 'nella',
            'nelle', 'tra', 'fra', 'delle', 'degli'}
    wa = {w.lower() for w in re.findall(r'\w+', a) if len(w) > 3 and w.lower() not in stop}
    wb = {w.lower() for w in re.findall(r'\w+', b) if len(w) > 3 and w.lower() not in stop}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _norm_piva(p):
    return re.sub(r'^[Ii][Tt]', '', (p or '').strip()).lstrip('0').upper()


def _fornitore_da_ddt(ddt):
    tutti_forn = Fornitore.query.all()

    if ddt.partita_iva:
        pn = _norm_piva(ddt.partita_iva)
        for fo in tutti_forn:
            if fo.partita_iva and _norm_piva(fo.partita_iva) == pn:
                return fo, pn

    nome_ddt = (ddt.fornitore or '').strip().lower()
    if nome_ddt:
        for fo in tutti_forn:
            if fo.nome and fo.nome.strip().lower() == nome_ddt:
                return fo, _norm_piva(fo.partita_iva)
        for fo in tutti_forn:
            if fo.nome:
                fn = fo.nome.strip().lower()
                if fn in nome_ddt or nome_ddt in fn or _sim_descrizione(nome_ddt, fn) > 0.5:
                    return fo, _norm_piva(fo.partita_iva)

    if ddt.commessa_id:
        ft_comm = Fattura.query.filter(Fattura.commessa_id == ddt.commessa_id,
                                       Fattura.tipo == 'passiva').all()
        for ft in ft_comm:
            if ft.partita_iva_fornitore:
                pn = _norm_piva(ft.partita_iva_fornitore)
                for fo in tutti_forn:
                    if fo.partita_iva and _norm_piva(fo.partita_iva) == pn:
                        return fo, pn

    return None, _norm_piva(ddt.partita_iva) if ddt.partita_iva else None
