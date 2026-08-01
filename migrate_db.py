import sqlite3

def migrate():
    conn = sqlite3.connect('data_grinder.db')
    cursor = conn.cursor()

    # Create categories table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        user_id VARCHAR NOT NULL
    )
    ''')

    # Add user_id to phrases
    try:
        cursor.execute('ALTER TABLE phrases ADD COLUMN user_id VARCHAR NOT NULL DEFAULT "default_user"')
    except sqlite3.OperationalError:
        pass # column might exist

    # Add user_id and category_id to cards
    try:
        cursor.execute('ALTER TABLE cards ADD COLUMN user_id VARCHAR NOT NULL DEFAULT "default_user"')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE cards ADD COLUMN category_id INTEGER REFERENCES categories(id)')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE cards ADD COLUMN has_seen_intro BOOLEAN NOT NULL DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE cards ADD COLUMN intro_phase INTEGER NOT NULL DEFAULT 0')
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE cards ADD COLUMN content_type VARCHAR NOT NULL DEFAULT 'text'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute('ALTER TABLE cards ADD COLUMN example VARCHAR')
    except sqlite3.OperationalError:
        pass

    # Add user_id to review_logs
    try:
        cursor.execute('ALTER TABLE review_logs ADD COLUMN user_id VARCHAR NOT NULL DEFAULT "default_user"')
    except sqlite3.OperationalError:
        pass

    # Add user_id to user_sessions
    try:
        cursor.execute('ALTER TABLE user_sessions ADD COLUMN user_id VARCHAR NOT NULL DEFAULT "default_user"')
    except sqlite3.OperationalError:
        pass

    # Add user_id to daily_sessions
    try:
        cursor.execute('ALTER TABLE daily_sessions ADD COLUMN user_id VARCHAR NOT NULL DEFAULT "default_user"')
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()
    print("Migration successful.")

if __name__ == '__main__':
    migrate()
