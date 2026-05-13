import random
from functools import wraps
from flask import redirect, url_for, render_template
from flask_login import current_user


def genera_otp():
    return str(random.randint(100000, 999999))


def ruolo_richiesto(sezione):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if not current_user.ha_accesso(sezione):
                return render_template('403.html'), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator


def ctx():
    from config import SEZIONI_LABEL
    return dict(sezioni=current_user.sezioni_accessibili(), sezioni_label=SEZIONI_LABEL)
