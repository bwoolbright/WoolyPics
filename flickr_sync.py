import os
import io
import time
import sqlite3
import requests
import re
from datetime import datetime, timezone
from PIL import Image
from google import genai
from google.genai import types
from flickrapi import FlickrAPI
import streamlit as st

DB_PATH = 'flickr_pipeline.db'

# Import helper functions directly from your tagger pipeline logic
from flickr_ai_tagger import (
    extract_exif_tags, 
    extract_location_tags, 
    analyze_image_with_gemini, 
    is_default_filename, 
    is_photo_logged, 
    log_photo_result,
    init_db
)

def run_sync_pipeline(progress_callback=None):
    """Fetches recent photos from Flickr, runs EXIF/Gemini AI tagging, and syncs to SQLite."""
    init_db()

    # Load keys securely from Streamlit secrets or environment
    api_key = st.secrets["flickr"]["api_key"]
    api_secret = st.secrets["flickr"]["api_secret"]
    gemini_key = st.secrets["gemini"]["api_key"] if "gemini" in st.secrets else os.getenv('GEMINI_API_KEY')
    user_id = st.secrets["flickr"]["user_id"]

    flickr = FlickrAPI(api_key, api_secret, format='parsed-json')
    gemini_client = genai.Client(api_key=gemini_key)

    # Fetch recent uploads from Flickr (e.g., last 30 photos)
    photos_res = flickr.photos.search(
        user_id=user_id,
        per_page=30,
        page=1,
        sort='date-posted-desc',
        extras='tags,description,geo,datetaken,camera,lens'
    )

    photos = photos_res.get('photos', {}).get('photo', [])
    total_photos = len(photos)
    new_count = 0

    for idx, photo in enumerate(photos, start=1):
        photo_id = photo['id']
        current_title = photo.get('title', 'Untitled')
        
        # Report progress if callback is provided
        if progress_callback:
            progress_callback(idx, total_photos, current_title)

        # Check if already processed in SQLite
        if is_photo_logged(photo_id):
            continue

        raw_tags = photo.get('tags', '')
        existing_tags = raw_tags.split() if isinstance(raw_tags, str) else []
        has_custom_title = not is_default_filename(current_title)

        try:
            # Extract EXIF and Location Tags
            exif_tags = extract_exif_tags(flickr, photo_id)
            geo_tags = extract_location_tags(flickr, photo_id)

            # Fetch image bytes for Gemini Vision
            sizes_res = flickr.photos.getSizes(photo_id=photo_id)
            sizes = sizes_res['sizes']['size']
            img_url = next((s['source'] for s in sizes if s.get('label') in ['Large', 'Medium 800', 'Medium 640']), sizes[-1]['source'])

            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(img_url, headers=headers)
            
            # Analyze image using Gemini
            metadata = analyze_image_with_gemini(gemini_client, response.content)
            
            final_title = current_title if has_custom_title else metadata.title
            all_generated_tags = exif_tags + geo_tags + metadata.tags
            existing_tags_set = set(t.lower() for t in existing_tags)
            new_tags = [t for t in all_generated_tags if t.lower() not in existing_tags_set]

            # Update Flickr metadata & tags via API
            flickr.photos.setMeta(photo_id=photo_id, title=final_title, description=metadata.description)
            tags_to_add = new_tags + ['ai-tagged']
            formatted_tags = ' '.join([f'"{tag}"' if ' ' in tag else tag for tag in tags_to_add])
            flickr.photos.addTags(photo_id=photo_id, tags=formatted_tags)

            # Store structured metadata in photo_metadata table
            date_taken = photo.get('datetaken', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
            camera_model = photo.get('camera', exif_tags[0] if exif_tags else '')
            lens_model = exif_tags[1] if len(exif_tags) > 1 else ''

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO photo_metadata (photo_id, date_taken, camera, lens)
                VALUES (?, ?, ?, ?)
            """, (photo_id, date_taken, camera_model, lens_model))
            conn.commit()
            conn.close()

            # Log success in audit table
            log_photo_result(photo_id, "SUCCESS", title=final_title, tags_added=tags_to_add)
            new_count += 1

        except Exception as e:
            log_photo_result(photo_id, "ERROR", error_msg=str(e))
            if "RESOURCE_EXHAUSTED" in str(e):
                if progress_callback:
                    progress_callback(idx, total_photos, "Quota exhausted! Stopping sync.")
                break

        time.sleep(0.3)

    return new_count