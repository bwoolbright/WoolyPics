import sqlite3
from flickrapi import FlickrAPI
import streamlit as st

DB_PATH = 'flickr_pipeline.db'

def ensure_geo_columns(cursor):
    """Ensure latitude and longitude columns exist in photo_metadata."""
    cursor.execute("PRAGMA table_info(photo_metadata);")
    columns = [row[1] for row in cursor.fetchall()]
    if 'latitude' not in columns:
        cursor.execute("ALTER TABLE photo_metadata ADD COLUMN latitude REAL;")
    if 'longitude' not in columns:
        cursor.execute("ALTER TABLE photo_metadata ADD COLUMN longitude REAL;")

def fast_backfill_coordinates():
    api_key = st.secrets["flickr"]["api_key"]
    api_secret = st.secrets["flickr"]["api_secret"]
    user_id = st.secrets["flickr"]["user_id"]

    flickr = FlickrAPI(api_key, api_secret, format='parsed-json')

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    ensure_geo_columns(cursor)
    conn.commit()

    print("Fetching geotagged photos directly from Flickr...")
    
    page = 1
    updated_count = 0
    
    while True:
        # Request only photos that have geo data, using max per_page (500)
        photos_res = flickr.photos.search(
            user_id=user_id,
            has_geo=1,
            per_page=500,
            page=page,
            extras='geo'
        )
        
        photos_data = photos_res.get('photos', {})
        photos = photos_data.get('photo', [])
        
        if not photos:
            break
            
        for photo in photos:
            photo_id = photo['id']
            lat = float(photo.get('latitude', 0)) or None
            lon = float(photo.get('longitude', 0)) or None
            
            if lat and lon:
                cursor.execute("""
                    INSERT INTO photo_metadata (photo_id, latitude, longitude)
                    VALUES (?, ?, ?)
                    ON CONFLICT(photo_id) DO UPDATE SET 
                        latitude = excluded.latitude, 
                        longitude = excluded.longitude
                """, (photo_id, lat, lon))
                updated_count += 1
                
        conn.commit()
        
        # Check if we've reached the last page
        if page >= photos_data.get('pages', 1):
            break
        page += 1

    conn.close()
    print(f"Fast backfill complete! Successfully mapped coordinates for {updated_count} photos.")

if __name__ == "__main__":
    fast_backfill_coordinates()