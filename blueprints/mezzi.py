from flask import Blueprint, render_template
from flask_login import login_required
from services.utils import ruolo_richiesto, ctx

bp = Blueprint('mezzi', __name__)


@bp.route('/mezzi')
@login_required
@ruolo_richiesto('mezzi')
def mezzi():
    return render_template('mezzi/mezzi.html', **ctx())
