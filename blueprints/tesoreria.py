from flask import Blueprint, render_template
from flask_login import login_required
from services.utils import ruolo_richiesto, ctx

bp = Blueprint('tesoreria', __name__)


@bp.route('/tesoreria')
@login_required
@ruolo_richiesto('tesoreria')
def tesoreria():
    return render_template('tesoreria/tesoreria.html', **ctx())
