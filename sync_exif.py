import os
import sqlite3
import time
import random
from flickrapi import FlickrAPI

API_KEY = "1a44c0ba9c221b8c5078c08fc0c4467d"
API_SECRET = "e144457lac48e9bd"
DB_PATH = "flickr_pipeline.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS photo_metadata (
            photo_id TEXT PRIMARY KEY,
            camera TEXT,
            lens TEXT,
            date_taken TEXT
        )
    """)
    conn.commit()
    conn.close()

def fetch_with_retry(flickr, photo_id, retries=5):
    """Fetches EXIF with exponential backoff on 429 Too Many Requests."""
    delay = 2
    for attempt in range(retries):
        try:
            rsp = flickr.photos.getExif(photo_id=photo_id)
            return rsp
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "Too Many Requests" in err_str:
                sleep_time = delay + random.uniform(0, 1)
                time.sleep(sleep_time)
                delay *= 2  # Exponential backoff
            else:
                break
    return None

def run():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get photos that haven't been successfully synced yet or have missing lens data
    cursor.execute("""
        SELECT p.photo_id FROM processed_photos p
        LEFT JOIN photo_metadata m ON p.photo_id = m.photo_id
        WHERE (m.photo_id IS NULL OR m.lens IS NULL OR m.lens = '' OR m.lens = 'Unknown / No Lens Data') 
        AND p.status = 'SUCCESS'
    """)
    photos = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not photos:
        print("✓ All photos already synced!")
        return

    print(f"Resuming EXIF sync for {len(photos)} photos with rate limiting...")
    flickr = FlickrAPI(API_KEY, API_SECRET, format="parsed-json")

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for idx, photo_id in enumerate(photos, 1):
        rsp = fetch_with_retry(flickr, photo_id)
        
        camera, lens, date_taken = "", "", ""
        if rsp:
            photo_exif = rsp.get("photo", {})
            camera = photo_exif.get("camera", "").strip()
            for exif_tag in photo_exif.get("exif", []):
                tag_label = exif_tag.get("label", "").lower()
                tag_name = exif_tag.get("tag", "").lower()
                tag_space = exif_tag.get("tagspace", "").lower()
                
                raw_val = exif_tag.get("raw", {})
                val = raw_val.get("_content", "").strip() if isinstance(raw_val, dict) else str(raw_val).strip()

                # Expanded lens matching to catch varied metadata tags
                if any(k in tag_label or k in tag_name or k in tag_space for k in ["lens", "lensmodel", "lensinfo"]):
                    if val and not lens:
                        lens = val
                elif "date and time (original)" in tag_label or tag_name == "datetimeoriginal":
                    date_taken = val

          # Fallback mapping that distinguishes GR II and GR III explicitly
            if not lens:
                cam_upper = camera.upper()
                if "RICOH GR III" in cam_upper or "GR III" in cam_upper:
                    lens = "Ricoh GR III (18.3mm f/2.8)"
                elif "RICOH GR II" in cam_upper or "GR II" in cam_upper:
                    lens = "Ricoh GR II (18.3mm f/2.8)"
                elif "X100V" in cam_upper:
                    lens = "Fujifilm X100V (23mm f/2)"
                elif "X100T" in cam_upper:
                    lens = "Fujifilm X100T (23mm f/2)"
                elif "X70" in cam_upper:
                    lens = "Fujifilm X70 (18.5mm f/2.8)"
                elif "RX100" in cam_upper or "DSC-RX100M2" in cam_upper:
                    lens = "Sony RX100 II (28-100mm)"

        cursor.execute("""
            INSERT OR REPLACE INTO photo_metadata (photo_id, camera, lens, date_taken)
            VALUES (?, ?, ?, ?)
        """, (photo_id, camera, lens, date_taken))

        if idx % 25 == 0:
            conn.commit()
            print(f"Synced {idx}/{len(photos)} photos...")

        # Gentle pacing to avoid triggering Flickr rate limits
        time.sleep(0.3)

    conn.commit()
    conn.close()
    print("✓ EXIF sync complete!")

if __name__ == "__main__":
    run()