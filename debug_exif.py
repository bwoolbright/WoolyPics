import sqlite3
import json
from flickrapi import FlickrAPI

API_KEY = "1a44c0ba9c221b8c5078c08fc0c4467d"
API_SECRET = "e144457lac48e9bd"
DB_PATH = "flickr_pipeline.db"

# Grab one photo_id that currently shows as Unknown in your dashboard
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("""
    SELECT p.photo_id FROM processed_photos p
    JOIN photo_metadata m ON p.photo_id = m.photo_id
    WHERE m.lens = '' OR m.lens IS NULL
    LIMIT 1
""")
row = cursor.fetchone()
conn.close()

if not row:
    print("No unknown lens photos found in DB to test.")
    exit()

photo_id = row[0]
print(f"Inspecting raw EXIF for photo_id: {photo_id}")

flickr = FlickrAPI(API_KEY, API_SECRET, format="parsed-json")
rsp = flickr.photos.getExif(photo_id=photo_id)

# Print all keys and tags returned by Flickr
photo_exif = rsp.get("photo", {})
print("Camera detected by API:", photo_exif.get("camera"))
print("\n--- All EXIF Tags Returned by Flickr ---")

for tag in photo_exif.get("exif", []):
    label = tag.get("label", "")
    tag_name = tag.get("tag", "")
    raw = tag.get("raw", {})
    val = raw.get("_content", "") if isinstance(raw, dict) else str(raw)
    print(f"Label: '{label}' | Tag: '{tag_name}' | Value: '{val}'")