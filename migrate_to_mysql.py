#!/usr/bin/env python3
"""
Migrazione one-shot: SQLite → MySQL
Uso: python3 migrate_to_mysql.py [--sqlite path/to/webapp.db]

Legge DATABASE_URL da .env (o da variabile d'ambiente).
Esempio .env:
    DATABASE_URL=mysql+pymysql://root:password@localhost:3306/prometeo
"""

import os
import sys
import sqlite3
import argparse

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import pymysql
except ImportError:
    sys.exit("PyMySQL non installato. Esegui: pip install PyMySQL")


def parse_mysql_url(url: str):
    """Estrae host, port, user, password, database dall'URI SQLAlchemy."""
    # mysql+pymysql://user:password@host:port/dbname
    url = url.replace('mysql+pymysql://', '')
    userinfo, hostinfo = url.split('@', 1)
    user, password = userinfo.split(':', 1)
    if '/' in hostinfo:
        hostport, dbname = hostinfo.split('/', 1)
    else:
        sys.exit("DATABASE_URL non contiene il nome del database.")
    if ':' in hostport:
        host, port = hostport.split(':', 1)
        port = int(port)
    else:
        host, port = hostport, 3306
    return host, port, user, password, dbname


# Ordine rispettando i vincoli di FK
TABLE_ORDER = [
    'users',
    'clienti',
    'commesse',
    'ordini',
    'righe_ordine',
    'fornitori',
    'fatture',
    'righe_fattura_attiva',
    'ddt',
    'righe_ddt',
    'costi_fornitori',
    'abbinati_ddt_fattura',
    'abbinati_ordine_subappalto',
    'dipendenti',
    'documenti_dipendenti',
    'nodi_organico',
    'app_config',
]


def migrate(sqlite_path: str, mysql_url: str):
    print(f"Source SQLite : {sqlite_path}")

    host, port, user, password, dbname = parse_mysql_url(mysql_url)
    print(f"Dest   MySQL  : {user}@{host}:{port}/{dbname}\n")

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row

    dst = pymysql.connect(
        host=host, port=port, user=user, password=password,
        database=dbname, charset='utf8mb4',
        autocommit=False,
    )
    cur_dst = dst.cursor()

    # Disabilita FK check su MySQL per l'import
    cur_dst.execute("SET FOREIGN_KEY_CHECKS = 0")

    # Tabelle presenti nel SQLite sorgente
    existing = {r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

    # Processa le tabelle nell'ordine definito, poi quelle rimanenti
    ordered = [t for t in TABLE_ORDER if t in existing]
    remaining = [t for t in existing if t not in TABLE_ORDER and not t.startswith('sqlite_')]
    all_tables = ordered + remaining

    total_rows = 0
    for table in all_tables:
        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: vuota, skip")
            continue

        cols = rows[0].keys()
        placeholders = ', '.join(['%s'] * len(cols))
        col_names = ', '.join(f'`{c}`' for c in cols)
        sql = f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders})"

        # Svuota la tabella di destinazione prima di inserire
        cur_dst.execute(f"DELETE FROM `{table}`")

        batch = [tuple(row) for row in rows]
        try:
            cur_dst.executemany(sql, batch)
            dst.commit()
        except Exception as e:
            dst.rollback()
            print(f"  ERRORE su {table}: {e}")
            continue

        # Reimposta AUTO_INCREMENT
        max_id_row = src.execute(f"SELECT MAX(id) FROM {table}").fetchone()
        if max_id_row and max_id_row[0] is not None:
            next_id = max_id_row[0] + 1
            cur_dst.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT = {next_id}")
            dst.commit()

        total_rows += len(batch)
        print(f"  {table}: {len(batch)} righe migrate")

    cur_dst.execute("SET FOREIGN_KEY_CHECKS = 1")
    dst.commit()

    src.close()
    dst.close()

    print(f"\nMigrazione completata: {total_rows} righe totali trasferite.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Migra dati da SQLite a MySQL')
    parser.add_argument(
        '--sqlite',
        default=os.path.join(os.path.dirname(__file__), 'instance', 'webapp.db'),
        help='Path al file SQLite sorgente (default: instance/webapp.db)',
    )
    args = parser.parse_args()

    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url.startswith('mysql'):
        sys.exit(
            "DATABASE_URL non configurata o non punta a MySQL.\n"
            "Imposta DATABASE_URL=mysql+pymysql://user:pass@host:port/db nel file .env"
        )

    if not os.path.exists(args.sqlite):
        sys.exit(f"File SQLite non trovato: {args.sqlite}")

    migrate(args.sqlite, db_url)
