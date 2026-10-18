import os
import sqlite3
from flickrapi import FlickrAPI

FLICKR_API_KEY = os.getenv('FLICKR_API_KEY', '1a44c0ba9c221b8c5078c08fc0c4467d')
FLICKR_API_SECRET = os.getenv('FLICKR_API_SECRET', 'e1444571ac48e9bd')
DB_PATH = 'flickr_pipeline.db'

def sync_full_library():
    flickr = FlickrAPI(FLICKR_API_KEY, FLICKR_API_SECRET, format='parsed-json')
    user_res = flickr.test.login()
    user_id = user_res['user']['id']

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get total page count
    res = flickr.photos.search(user_id=user_id, per_page=500, page=1)
    total_pages = int(res['photos']['pages'])
    total_photos = int(res['photos']['total'])
    
    print(f"Syncing EXIF & basic metadata for all {total_photos} photos...")

    for page in range(1, total_pages + 1):
        photos_res = flickr.photos.search(
            user_id=user_id,
            per_page=500,
            page=page,
            extras='tags,description,date_taken'
        )
        for photo in photos_res['photos']['photo']:
            photo_id = photo['id']
            title = photo.get('title', '')
            tags = photo.get('tags', '')

            # Insert basic metadata if record doesn't exist yet
            cursor.execute('''
                INSERT OR IGNORE INTO processed_photos (photo_id, status, title, tags_added, processed_at)
                VALUES (?, 'PENDING', ?, ?, CURRENT_TIMESTAMP)
            ''', (photo_id, title, tags))
        conn.commit()
        print(f"  Synced page {page}/{total_pages}")

    conn.close()
    print("Full photostream sync complete!")

if __name__ == '__main__':
    sync_full_library()