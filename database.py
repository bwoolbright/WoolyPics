import sqlite3
import pandas as pd

DB_PATH = 'flickr_pipeline.db'

def ensure_indexes():
    """Ensure performance indices and geo columns exist on the SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_photos_status ON processed_photos(status);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_photo_metadata_id ON photo_metadata(photo_id);")
        
        # Ensure latitude and longitude columns exist in photo_metadata
        cursor.execute("PRAGMA table_info(photo_metadata);")
        columns = [row[1] for row in cursor.fetchall()]
        if 'latitude' not in columns:
            cursor.execute("ALTER TABLE photo_metadata ADD COLUMN latitude REAL;")
        if 'longitude' not in columns:
            cursor.execute("ALTER TABLE photo_metadata ADD COLUMN longitude REAL;")
            
        conn.commit()
        conn.close()
    except Exception:
        pass

def load_data():
    ensure_indexes()
    conn = sqlite3.connect(DB_PATH)
    query = """
        SELECT 
            COALESCE(p.photo_id, m.photo_id) as photo_id,
            COALESCE(p.status, 'SUCCESS') as status,
            COALESCE(p.title, '') as title,
            p.tags_added,
            p.processed_at,
            COALESCE(NULLIF(m.camera, ''), 'Unknown / Missing EXIF') as camera,
            COALESCE(NULLIF(m.lens, ''), 'Unknown / No Lens Data') as lens,
            m.date_taken,
            m.latitude,
            m.longitude
        FROM photo_metadata m
        LEFT JOIN processed_photos p ON m.photo_id = p.photo_id
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    if 'date_taken' in df.columns:
        cleaned_dates = df['date_taken'].astype(str).str.replace(':', '-', 2)
        df['date_taken'] = pd.to_datetime(cleaned_dates, errors='coerce')
        if df['date_taken'].dt.tz is not None:
            df['date_taken'] = df['date_taken'].dt.tz_localize(None)
            
    return df

def get_total_photos_count():
    conn = sqlite3.connect(DB_PATH)
    total_photos_df = pd.read_sql_query("SELECT status FROM processed_photos", conn)
    conn.close()
    return len(total_photos_df), len(total_photos_df[total_photos_df['status'] == 'UNPROCESSED'])