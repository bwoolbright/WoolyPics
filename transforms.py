import re
import pandas as pd

def format_lens_and_focal(text: str) -> str:
    if not text:
        return text
    text = re.sub(r'\b(100|70|18|24|50|15|16)(400|300|50|70|200|85|105|55)mm\b', r'\1-\2mm', text, flags=re.IGNORECASE)
    text = re.sub(r'\bf/([0-9])([0-9])\b', r'f/\1.\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\bf([0-9])([0-9])\b', r'f/\1.\2', text, flags=re.IGNORECASE)
    text = re.sub(r'\bf([0-9])([0-9])-([0-9])([0-9])\b', r'f/\1.\2-\3.\4', text, flags=re.IGNORECASE)
    return text.strip()

def enrich_lens_brand(row):
    lens = str(row['lens']).strip()
    camera = str(row['camera']).upper()
    if lens in ['Unknown / No Lens Data', 'Unknown / Missing EXIF']:
        return lens
    if '18.3mm' in lens or 'GR LENS' in lens:
        if 'GR III' in camera:
            return "Ricoh GR III (18.3mm f/2.8)"
        elif 'GR II' in camera:
            return "Ricoh GR II (18.3mm f/2.8)"
    lens_lower = lens.lower()
    if any(b in lens_lower for b in ['nikkor', 'fujinon', 'fujifilm', 'zeiss', 'tamron', 'viltrox', 'ttartisan', 'ricoh']):
        return format_lens_and_focal(lens)
    if re.search(r'\d+mm', lens, re.IGNORECASE):
        if 'FUJIFILM' in camera or 'FUJI' in camera:
            return format_lens_and_focal(f"Fujifilm {lens}")
        elif 'NIKON' in camera:
            return format_lens_and_focal(f"Nikkor {lens}")
        elif 'RICOH' in camera:
            if 'GR III' in camera:
                return "Ricoh GR III (18.3mm f/2.8)"
            elif 'GR II' in camera:
                return "Ricoh GR II (18.3mm f/2.8)"
            return format_lens_and_focal(f"Ricoh {lens}")
        elif 'SONY' in camera:
            return format_lens_and_focal(f"Sony {lens}")
    return format_lens_and_focal(lens)

def categorize_lens_family(row):
    lens = str(row['enriched_lens']).lower()
    camera = str(row['camera']).lower()
    if 'unknown' in lens or 'unknown' in camera:
        return 'Unclassified / No Data'
    elif 'fuji' in lens or 'fuji' in camera or 'xf' in lens:
        return 'Fujifilm Ecosystem'
    elif 'nikon' in camera or 'nikkor' in lens or 'z ' in lens or '100-400mm' in lens:
        return 'Nikon Ecosystem'
    elif 'ricoh' in camera or 'gr ' in lens or 'x70' in camera or 'rx100' in camera or 'phone' in camera or 'pixel' in camera:
        return 'Fixed-Lens & Mobile'
    else:
        return 'Other Lenses'

def get_crop_factor(camera_str: str) -> float:
    cam = camera_str.upper()
    if any(m in cam for m in ['FUJIFILM', 'FUJI', 'X100', 'X70', 'X-']):
        return 1.5
    if any(m in cam for m in ['PIXEL', 'PHONE', 'IPHONE', 'SMARTPHONE', 'MOBILE']):
        return 8.3  
    return 1.0

def format_focal_with_equivalent(focal_str: str, camera_str: str) -> str:
    crop = get_crop_factor(camera_str)
    focal_clean = focal_str.lower().strip()
    if crop == 1.0:
        return focal_clean
    single_match = re.search(r'^(\d+)\s*mm$', focal_clean, re.IGNORECASE)
    if single_match:
        val = int(single_match.group(1)) * crop
        return f"{focal_clean} ({int(round(val))}mm Equiv)"
    return focal_clean

def extract_exposure_from_tags(tags_list):
    focal_lengths, iso_vals, apertures, shutter_speeds = [], [], [], []
    for tag in tags_list:
        tag_clean = tag.strip().lower()
        fl_match = re.search(r'\b(\d+(?:\-\d+)?\s*mm)\b', tag_clean, re.IGNORECASE)
        if fl_match:
            focal_lengths.append(format_lens_and_focal(fl_match.group(1)).lower())
        else:
            unhyphenated_fl = re.search(r'\b(100400|1850|70300|2470|1680)\s*mm\b', tag_clean, re.IGNORECASE)
            if unhyphenated_fl:
                focal_lengths.append(format_lens_and_focal(unhyphenated_fl.group(0)).lower())
        iso_match = re.search(r'\biso\s*(\d+)\b', tag_clean, re.IGNORECASE)
        if iso_match:
            iso_vals.append(f"ISO {iso_match.group(1)}")
        ap_match = re.search(r'\b(f/?\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?)\b', tag_clean, re.IGNORECASE)
        if ap_match:
            apertures.append(format_lens_and_focal(ap_match.group(1)).lower())
        ss_match = re.search(r'\b(1/\d+s?|\d+(?:\.\d+)?s)\b', tag_clean, re.IGNORECASE)
        if ss_match:
            shutter_speeds.append(ss_match.group(1))
    return focal_lengths, iso_vals, apertures, shutter_speeds

def get_top_n_for_pie(series_data, name_col, count_col, n=5):
    df_raw = pd.Series(series_data).value_counts().reset_index()
    df_raw.columns = [name_col, count_col]
    if len(df_raw) > n:
        top = df_raw.head(n)
        other_sum = df_raw.tail(len(df_raw) - n)[count_col].sum()
        other_row = pd.DataFrame([{name_col: 'Other', count_col: other_sum}])
        return pd.concat([top, other_row], ignore_index=True)
    return df_raw