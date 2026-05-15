import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "..", "src", "data.db")

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if column exists
    cursor.execute("PRAGMA table_info(user_settings)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "living_expenses_threshold" not in columns:
        cursor.execute("ALTER TABLE user_settings ADD COLUMN living_expenses_threshold BIGINT DEFAULT 1000000;")
        conn.commit()
        print("Successfully added living_expenses_threshold to user_settings.")
    else:
        print("Column living_expenses_threshold already exists.")
        
except Exception as e:
    print(f"Error: {e}")
finally:
    if 'conn' in locals():
        conn.close()
