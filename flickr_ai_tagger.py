import os
import sys
import argparse
import json
import io
import time
import sqlite3
import requests
import re
import functools
from datetime import datetime, timezone
from PIL import Image
from google import genai
from google.genai import types
from flickrapi import FlickrAPI
from pydantic import BaseModel

# Force immediate log flushing to output.log
print = functools.partial(print, flush=True)

try:
    from geopy.geocoders import Nominatim
    GEOPY_AVAILABLE = True
except ImportError:
    GEOPY_AVAILABLE = False

# ==========================================
# CONFIGURATION
# ==========================================
FLICKR_API_KEY = os.getenv('FLICKR_API_KEY', '1a44c0ba9c221b8c5078c08fc0c4467d')
FLICKR_API_SECRET = os.getenv('FLICKR_API_SECRET', 'e1444571ac48e9bd')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', 'AQ.Ab8RN6IOwujzsnBGSZY9kYAxRUtZ4NbgDTHJTEv9tt-NONIa4A')

DB_PATH = 'flickr_pipeline.db'

class PhotoMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]

# ==========================================
# DATABASE AUDIT LOG (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS processed_photos (
            photo_id TEXT PRIMARY KEY,
            status TEXT,
            title TEXT,
            tags_added TEXT,
            error_message TEXT,
            processed_at TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def is_photo_logged(photo_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT status FROM processed_photos WHERE photo_id = ? AND status = "SUCCESS"', (photo_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def log_photo_result(photo_id: str, status: str, title: str = "", tags_added: list = None, error_msg: str = ""):
    tags_str = ", ".join(tags_added) if tags_added else ""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO processed_photos (photo_id, status, title, tags_added, error_message, processed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (photo_id, status, title, tags_str, error_msg, datetime.now(timezone.utc)))
    conn.commit()
    conn.close()

# ==========================================
# HELPER FUNCTIONS & EXIF / LOCATION PARSERS
# ==========================================
def is_default_filename(title: str) -> bool:
    # Expanded pattern to handle Fujifilm (DSCF/_DSF), Sony/Nikon/Canon, etc.
    pattern = r'^(dsc|dscf|_dsf|img|pxl|gopro|sam|mov|vid|nik|fx|canon)[_-]?\d+(\.[a-z0-9]+)?$'
    return bool(re.match(pattern, title.strip(), re.IGNORECASE))

def extract_exif_tags(flickr: FlickrAPI, photo_id: str) -> list[str]:
    exif_tags = []
    try:
        exif_res = flickr.photos.getExif(photo_id=photo_id)
        photo_exif = exif_res.get('photo', {}).get('exif', [])

        exif_dict = {}
        for item in photo_exif:
            tag = item.get('tag')
            raw_val = item.get('raw', {}).get('_content', '').strip()
            clean_val = item.get('clean', {}).get('_content', raw_val).strip()
            exif_dict[tag] = clean_val or raw_val

        if 'Model' in exif_dict:
            model_clean = re.sub(r'[^a-zA-Z0-9\s-]', '', exif_dict['Model']).strip()
            exif_tags.append(model_clean)

        if 'LensModel' in exif_dict or 'Lens' in exif_dict:
            lens_raw = exif_dict.get('LensModel') or exif_dict.get('Lens')
            lens_clean = re.sub(r'[^a-zA-Z0-9\s-]', '', lens_raw).strip()
            exif_tags.append(lens_clean)

        if 'FocalLength' in exif_dict:
            exif_tags.append(exif_dict['FocalLength'].replace(' ', ''))

        if 'FNumber' in exif_dict:
            fnum = exif_dict['FNumber'].lower().replace(' ', '')
            exif_tags.append(f"f/{fnum}" if not fnum.startswith('f/') else fnum)

        if 'ExposureTime' in exif_dict:
            exp = exif_dict['ExposureTime'].replace(' ', '')
            exif_tags.append(f"{exp}s" if not exp.endswith('s') else exp)

        if 'ISO' in exif_dict or 'ISOSpeedRatings' in exif_dict:
            iso_val = exif_dict.get('ISO') or exif_dict.get('ISOSpeedRatings')
            exif_tags.append(f"ISO{iso_val}")

    except Exception:
        pass

    return exif_tags

def extract_location_tags(flickr: FlickrAPI, photo_id: str) -> list[str]:
    if not GEOPY_AVAILABLE:
        return []

    geo_tags = []
    try:
        geo_res = flickr.photos.geo.getLocation(photo_id=photo_id)
        location = geo_res.get('photo', {}).get('location', {})
        lat = location.get('latitude')
        lon = location.get('longitude')

        if lat and lon and (float(lat) != 0 or float(lon) != 0):
            geolocator = Nominatim(user_agent="flickr_ai_tagger")
            loc_data = geolocator.reverse((lat, lon), exactly_one=True, language='en')
            if loc_data and 'address' in loc_data.raw:
                address = loc_data.raw['address']
                city = address.get('city') or address.get('town') or address.get('village')
                state = address.get('state')
                country = address.get('country')

                for loc_item in [city, state, country]:
                    if loc_item:
                        geo_tags.append(loc_item)
    except Exception:
        pass

    return geo_tags

# ==========================================
# GEMINI VISION ANALYSIS
# ==========================================
def analyze_image_with_gemini(client: genai.Client, image_bytes: bytes, max_retries=3) -> PhotoMetadata:
    pil_image = Image.open(io.BytesIO(image_bytes))
    if pil_image.mode != 'RGB':
        pil_image = pil_image.convert('RGB')

    prompt = (
        "Analyze this image and provide clear, natural metadata for a personal Flickr photostream:\n"
        "1. TITLE: A clean, descriptive title (3-7 words) identifying what or where the main subject is. "
        "Avoid flowery, poetic, or gallery-style language.\n"
        "2. DESCRIPTION: A simple 1-2 sentence description explaining what is happening in the scene, "
        "noting key details like subject, setting, or visual context.\n"
        "3. TAGS: 10-15 concise, comma-separated search tags (specific subjects, key objects, location/environment type, "
        "dominant colors, and broad photography style).\n\n"
        "Formatting rules:\n"
        "- Do not use words like 'fine art', 'curator', 'ethereal', 'juxtaposition', or 'tapestry'.\n"
        "- Keep tags clean: lowercase, single words or short compound terms (e.g., street-photography, golden-hour, landscape)."
    )

    primary_model = 'gemini-3.8-flash'

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=primary_model,
                contents=[pil_image, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=PhotoMetadata,
                    temperature=0.2,
                )
            )
            data = json.loads(response.text)
            return PhotoMetadata(**data)

        except Exception as e:
            err_msg = str(e)
            if any(code in err_msg for code in ["503", "429", "UNAVAILABLE"]) and attempt < max_retries:
                wait_time = attempt * 3
                print(f"    [!] Gemini busy (attempt {attempt}/{max_retries}). Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise e

    raise Exception("Gemini API call failed after retries.")

# ==========================================
# MAIN EXECUTION LOOP WITH PAGINATION
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="Flickr AI Portfolio Metadata Generator")
    parser.add_argument("--dry-run", action="store_true", help="Run analysis and print metadata without applying updates to Flickr.")
    parser.add_argument("--per-page", type=int, default=500, help="Number of photos per page call (max 500).")
    args = parser.parse_args()

    init_db()

    print("Initializing Flickr & Gemini clients...")
    flickr = FlickrAPI(FLICKR_API_KEY, FLICKR_API_SECRET, format='parsed-json')
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

    user_res = flickr.test.login()
    user_id = user_res['user']['id']

    # 1. First fetch to get total page count
    initial_res = flickr.photos.search(
        user_id=user_id,
        per_page=args.per_page,
        page=1,
        sort='date-posted-desc',
        extras='tags,description,geo'
    )

    total_photos = int(initial_res['photos']['total'])
    total_pages = int(initial_res['photos']['pages'])
    
    print(f"Found {total_photos} total photos across {total_pages} page(s). (Dry-Run: {args.dry_run})")

    global_processed_count = 0

    for current_page in range(1, total_pages + 1):
        print(f"\n==========================================")
        print(f" FETCHING PAGE {current_page} OF {total_pages}")
        print(f"==========================================")

        photos_res = flickr.photos.search(
            user_id=user_id,
            per_page=args.per_page,
            page=current_page,
            sort='date-posted-desc',
            extras='tags,description,geo'
        )

        photos = photos_res['photos']['photo']
        if not photos:
            break

        for idx, photo in enumerate(photos, start=1):
            global_processed_count += 1
            photo_id = photo['id']
            current_title = photo.get('title', 'Untitled')
            
            raw_tags = photo.get('tags', '')
            existing_tags = raw_tags.split() if isinstance(raw_tags, str) else []

            # Check DB or marker tag for skip
            if 'ai-tagged' in existing_tags or is_photo_logged(photo_id):
                print(f"[{global_processed_count}/{total_photos}] Photo ID: {photo_id} -> Skip: Already processed.")
                continue

            has_custom_title = not is_default_filename(current_title)

            print(f"\n[{global_processed_count}/{total_photos}] Processing Photo ID: {photo_id} (Page {current_page})")
            print(f"Current Title: '{current_title}' | Existing Tag Count: {len(existing_tags)}")

            try:
                exif_tags = extract_exif_tags(flickr, photo_id)
                geo_tags = extract_location_tags(flickr, photo_id)
                if exif_tags:
                    print(f" -> EXIF Tags Found: {', '.join(exif_tags)}")
                if geo_tags:
                    print(f" -> Location Tags Found: {', '.join(geo_tags)}")

                sizes_res = flickr.photos.getSizes(photo_id=photo_id)
                sizes = sizes_res['sizes']['size']
                img_url = next((s['source'] for s in sizes if s.get('label') in ['Large', 'Medium 800', 'Medium 640']), sizes[-1]['source'])

                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(img_url, headers=headers)
                img_data = response.content

                metadata = analyze_image_with_gemini(gemini_client, img_data)
                
                final_title = current_title if has_custom_title else metadata.title

                all_generated_tags = exif_tags + geo_tags + metadata.tags
                existing_tags_set = set(t.lower() for t in existing_tags)
                new_tags = [t for t in all_generated_tags if t.lower() not in existing_tags_set]

                print(f" -> Title: {final_title}")
                print(f" -> Description: {metadata.description}")
                print(f" -> New Tags Added ({len(new_tags)}): {', '.join(new_tags)}")

                if not args.dry_run:
                    flickr.photos.setMeta(photo_id=photo_id, title=final_title, description=metadata.description)

                    tags_to_add = new_tags + ['ai-tagged']
                    formatted_tags = ' '.join([f'"{tag}"' if ' ' in tag else tag for tag in tags_to_add])
                    flickr.photos.addTags(photo_id=photo_id, tags=formatted_tags)

                    log_photo_result(photo_id, "SUCCESS", title=final_title, tags_added=tags_to_add)
                    print(f" Successfully updated photo {photo_id}.")
                else:
                    print(f" [DRY RUN] Would update photo {photo_id}.")

            except Exception as e:
                err_msg = str(e)
                print(f" Error processing photo {photo_id}: {err_msg}")
                log_photo_result(photo_id, "ERROR", error_msg=err_msg)
                
                if "RESOURCE_EXHAUSTED" in err_msg or "Quota exceeded" in err_msg:
                    print("\n[!] Quota exhausted. Stopping batch.")
                    return

            time.sleep(0.5)

    print("\nEntire photostream pagination complete.")

if __name__ == '__main__':
    main()