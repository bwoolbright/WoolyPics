import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta

from database import load_data, get_total_photos_count
from flickr_sync import run_sync_pipeline  # <--- Import your sync function
from transforms import (
    enrich_lens_brand, categorize_lens_family, 
    format_focal_with_equivalent, extract_exposure_from_tags, 
    get_top_n_for_pie
)

# Set page configuration
st.set_page_config(
    page_title="Photostream Analytics",
    page_icon="📷",
    layout="wide"
)

# Title and Header
st.title("Photostream Portfolio Analytics")
st.markdown("Detailed breakdown of gear, visual tags, and photo metadata from `flickr_pipeline.db`.")

# Load Data (cached via database module)
df = load_data()

if df.empty:
    st.warning("No successfully processed photos found in `flickr_pipeline.db` yet.")
    st.stop()

# --- SIDEBAR TIME RANGE SELECTBOX ---
st.sidebar.header("Time Range Filter")

valid_dates = df['date_taken'].dropna()
if not valid_dates.empty:
    st.sidebar.caption(f"Library Span: {valid_dates.min().strftime('%b %Y')} – {valid_dates.max().strftime('%b %Y')}")

# Dynamically extract unique years present in the photostream (sorted newest to oldest)
available_years = sorted(valid_dates.dt.year.unique(), reverse=True) if not valid_dates.empty else []
year_options = [str(y) for y in available_years]

time_options = [
    "All Time", 
    "Last 30 Days", 
    "Last 90 Days", 
    "Past 6 Months", 
    "Past 12 Months", 
    "Year to Date", 
    "Previous Calendar Year"
] + year_options

time_filter = st.sidebar.selectbox(
    "Select Period",
    options=time_options,
    index=0
)

now = pd.Timestamp.now().normalize()
if time_filter == "Last 30 Days":
    start_date = now - timedelta(days=30)
    df = df[df['date_taken'] >= start_date]
elif time_filter == "Last 90 Days":
    start_date = now - timedelta(days=90)
    df = df[df['date_taken'] >= start_date]
elif time_filter == "Past 6 Months":
    start_date = now - timedelta(days=180)
    df = df[df['date_taken'] >= start_date]
elif time_filter == "Past 12 Months":
    start_date = now - timedelta(days=365)
    df = df[df['date_taken'] >= start_date]
elif time_filter == "Year to Date":
    start_date = pd.Timestamp(f"{now.year}-01-01")
    df = df[df['date_taken'] >= start_date]
elif time_filter == "Previous Calendar Year":
    prev_year = now.year - 1
    df = df[(df['date_taken'] >= pd.Timestamp(f"{prev_year}-01-01")) & (df['date_taken'] <= pd.Timestamp(f"{prev_year}-12-31"))]
elif time_filter.isdigit():
    target_year = int(time_filter)
    df = df[(df['date_taken'] >= pd.Timestamp(f"{target_year}-01-01")) & (df['date_taken'] <= pd.Timestamp(f"{target_year}-12-31"))]

# Apply Enrichment Transforms so we can evaluate lenses properly
df['enriched_lens'] = df.apply(enrich_lens_brand, axis=1)
df['lens_family'] = df.apply(categorize_lens_family, axis=1)

# --- INTERACTIVE CROSS-FILTERING STATE SETUP ---
if 'selected_camera' not in st.session_state:
    st.session_state.selected_camera = None

if st.session_state.selected_camera:
    st.sidebar.info(f"Filtering by Camera: **{st.session_state.selected_camera}**")
    if st.sidebar.button("Clear Camera Filter", use_container_width=True):
        st.session_state.selected_camera = None
        st.rerun()

working_df = df.copy()
if st.session_state.selected_camera:
    working_df = working_df[working_df['camera'] == st.session_state.selected_camera]

st.sidebar.divider()
st.sidebar.markdown(f"**Filtered Photos:** {len(working_df)}")

# Calculate Top Camera & Lens for the active filter range
if not working_df.empty:
    top_cam = working_df['camera'].mode()
    top_cam_str = top_cam.iloc[0] if not top_cam.empty else "N/A"
    
    # Exclude fixed-lens and missing data when calculating top interchangeable lens
    valid_lenses = working_df[
        (~working_df['lens_family'].isin(['Fixed-Lens & Mobile', 'Unclassified / No Data'])) & 
        (~working_df['enriched_lens'].isin(['Unknown / No Lens Data', 'Unknown / Missing EXIF', '']))
    ]
    top_lens = valid_lenses['enriched_lens'].mode() if not valid_lenses.empty else pd.Series()
    top_lens_str = top_lens.iloc[0] if not top_lens.empty else "N/A"

    st.sidebar.markdown("**Top Gear in Range:**")
    st.sidebar.caption(f"📷 **Camera:** {top_cam_str}\n\n🔍 **Lens:** {top_lens_str}")

if working_df.empty:
    st.warning(f"No photos match the selected time range ('{time_filter}'). Try selecting 'All Time'.")
    st.stop()

# --- SIDEBAR FLICKR SYNC ---
st.sidebar.divider()
st.sidebar.subheader("Flickr Sync")

if st.sidebar.button("🔄 Fetch New Uploads", use_container_width=True):
    # Use st.status for a clean, collapsible live container in the sidebar
    with st.sidebar.status("Initializing sync...", expanded=True) as status:
        progress_bar = st.progress(0)
        status_text = st.empty()

        def update_progress(current, total, title):
            pct = int((current / total) * 100) if total > 0 else 0
            progress_bar.progress(pct)
            status_text.markdown(f"**Processing ({current}/{total}):** `{title[:25]}...`")

        try:
            added = run_sync_pipeline(progress_callback=update_progress)
            
            progress_bar.progress(100)
            status.update(label="Sync complete!", state="complete", expanded=False)
            
            st.cache_data.clear()
            st.success(f"Successfully synced! Added {added} new photos.")
            st.rerun()
        except Exception as e:
            status.update(label="Sync failed", state="error", expanded=True)
            st.error(f"Error: {e}")

# --- TOP LEVEL METRICS ---
total_photos, pending_count = get_total_photos_count()
processed_count = len(working_df)

all_tags_raw = [
    tag.strip() 
    for tags in working_df['tags_added'].dropna() 
    for tag in tags.split(',') 
    if tag.strip()
]
unique_tags = len(set(all_tags_raw))

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Photostream", total_photos)
col2.metric("AI Processed", processed_count)
col3.metric("Pending Enrichment", pending_count)
col4.metric("Unique Tags", unique_tags)

st.divider()

all_focals, all_isos, all_aps, all_shutters = [], [], [], []
all_focals_native_and_equiv = []

for idx, row in working_df.iterrows():
    tags_str = row['tags_added']
    camera_name = row['camera']
    if pd.isna(tags_str):
        continue
    t_list = [t.strip() for t in tags_str.split(',') if t.strip()]
    f_l, i_v, a_v, s_v = extract_exposure_from_tags(t_list)
    
    all_focals.extend(f_l)
    all_isos.extend(i_v)
    all_aps.extend(a_v)
    all_shutters.extend(s_v)
    
    for fl in f_l:
        if '-' in fl:
            continue
        formatted_fl = format_focal_with_equivalent(fl, camera_name)
        all_focals_native_and_equiv.append(formatted_fl)

# --- GEOGRAPHIC MAP SECTION ---
st.header("Capture Locations")

if 'latitude' in df.columns and 'longitude' in df.columns:
    map_df = df.dropna(subset=['latitude', 'longitude']).copy()
    map_df['latitude'] = pd.to_numeric(map_df['latitude'], errors='coerce')
    map_df['longitude'] = pd.to_numeric(map_df['longitude'], errors='coerce')
    map_df = map_df[(map_df['latitude'].notna()) & (map_df['longitude'].notna())]
    map_df = map_df[(map_df['latitude'] != 0) & (map_df['longitude'] != 0)]
else:
    map_df = pd.DataFrame()

st.caption(f"Map status: {len(map_df)} points loaded for visualization.")

if not map_df.empty:
    # Calculate the geographic center of your photos
    center_lat = map_df['latitude'].mean()
    center_lon = map_df['longitude'].mean()
    
    fig_map = px.scatter_map(
        map_df,
        lat="latitude",
        lon="longitude",
        hover_name="title",
        hover_data=["camera", "date_taken"],
        center={"lat": center_lat, "lon": center_lon},
        zoom=4,
        height=450,
        color_discrete_sequence=["#FF4B4B"]
    )
    fig_map.update_layout(
        map_style="open-street-map",
        margin=dict(l=0, r=0, t=0, b=0)
    )
    st.plotly_chart(fig_map, use_container_width=True)
else:
    st.info("No GPS coordinate data found.")
    
# --- GEAR DISTRIBUTION CHART (TOP 10 + EXPANDED TAIL) ---
st.header("Gear Distribution")
st.caption("💡 *Tip: Click any bar in the Camera chart below to filter the entire dashboard by that specific camera body!*")

cam_df = working_df['camera'].value_counts().reset_index()
cam_df.columns = ['Camera', 'Count']

# Separate Top 10 from the rest of the long tail
total_cameras = len(cam_df)
top_cam_df = cam_df.head(10)
other_cam_df = cam_df.tail(len(cam_df) - 10) if total_cameras > 10 else pd.DataFrame()

# Render the primary Top 10 Chart
fig_cam = px.bar(
    top_cam_df, x='Count', y='Camera', orientation='h', text='Count',
    title=f"Cameras Used (Showing Top 10 of {total_cameras})", color='Count', color_continuous_scale='Blues'
)
fig_cam.update_traces(texttemplate='%{text}', textposition='outside')
fig_cam.update_layout(height=max(350, len(top_cam_df) * 32), yaxis={'categoryorder': 'total ascending'}, margin=dict(l=20, r=40, t=40, b=20))

cam_selection = st.plotly_chart(fig_cam, use_container_width=True, on_select="rerun", selection_mode="points")

if cam_selection and "selection" in cam_selection and cam_selection["selection"]["points"]:
    selected_point = cam_selection["selection"]["points"][0]
    if "y" in selected_point:
        clicked_cam = selected_point["y"]
        if st.session_state.selected_camera != clicked_cam:
            st.session_state.selected_camera = clicked_cam
            st.rerun()

# If there are more than 10 cameras, tuck the long tail neatly inside an expander
if not other_cam_df.empty:
    with st.expander(f"📷 View Remaining Cameras ({len(other_cam_df)} other bodies / older devices)"):
        st.dataframe(
            other_cam_df.rename(columns={'Camera': 'Camera Body', 'Count': 'Shots'}),
            use_container_width=True,
            hide_index=True
        )

st.markdown("<br>", unsafe_allow_html=True)

st.subheader("Interchangeable Lenses by Ecosystem & Brand")
# Excluded 'Fixed-Lens & Mobile' so only actual lens systems appear here
families = ['Nikon Ecosystem', 'Fujifilm Ecosystem', 'Other Lenses']
color_scales = ['Greens', 'Teal', 'Purples']

for family, color_scale in zip(families, color_scales):
    fam_df = working_df[working_df['lens_family'] == family]
    if fam_df.empty:
        continue
        
    lens_sub_df = fam_df['enriched_lens'].value_counts().reset_index()
    lens_sub_df.columns = ['Lens', 'Count']
    
    total_items = len(lens_sub_df)
    show_all_lens = False
    
    if total_items > 10:
        col_lbl, col_chk = st.columns([3, 1])
        with col_lbl:
            st.markdown(f"**{family}** (Showing Top 10 of {total_items})")
        with col_chk:
            show_all_lens = st.checkbox("Expand All", key=f"expand_lens_{family}")
        plot_df = lens_sub_df if show_all_lens else lens_sub_df.head(10)
    else:
        plot_df = lens_sub_df

    fig_sub = px.bar(
        plot_df, x='Count', y='Lens', orientation='h', text='Count',
        title=f"{family} Breakdown", color='Count', color_continuous_scale=color_scale
    )
    fig_sub.update_traces(texttemplate='%{text}', textposition='outside')
    fig_sub.update_layout(
        height=max(250, len(plot_df) * 32),
        yaxis={'categoryorder': 'total ascending'},
        margin=dict(l=20, r=40, t=40, b=20)
    )
    st.plotly_chart(fig_sub, use_container_width=True)
    st.markdown("<br>", unsafe_allow_html=True)

st.divider()

st.subheader("Most Frequently Shot Focal Lengths (Top 5)")

if all_focals_native_and_equiv:
    all_fl_df = pd.Series(all_focals_native_and_equiv).value_counts().reset_index()
    all_fl_df.columns = ['Focal Length', 'Count']
    
    total_fl = len(all_fl_df)
    show_all_fl = False
    if total_fl > 5:
        c_l, c_c = st.columns([3, 1])
        with c_l:
            st.markdown(f"**Focal Lengths** (Showing Top 5 of {total_fl})")
        with c_c:
            show_all_fl = st.checkbox("Expand All", key="expand_all_fl")
        plot_fl_df = all_fl_df if show_all_fl else all_fl_df.head(5)
    else:
        plot_fl_df = all_fl_df

    fig_all_fl = px.bar(
        plot_fl_df, 
        x='Count', 
        y='Focal Length', 
        orientation='h', 
        text='Count',
        title="Single Focal Length Frequency (with Crop & Phone Equiv)", 
        color='Count', 
        color_continuous_scale='Sunset'
    )
    fig_all_fl.update_traces(texttemplate='%{text}', textposition='outside')
    fig_all_fl.update_layout(
        height=max(250, len(plot_fl_df) * 30),
        yaxis={'categoryorder': 'total ascending'},
        margin=dict(l=20, r=40, t=40, b=20)
    )
    st.plotly_chart(fig_all_fl, use_container_width=True)
else:
    st.info("No single focal length data extracted from tags yet.")

st.divider()

st.header("Exposure Breakdown")

exp_col1, exp_col2, exp_col3 = st.columns(3)

with exp_col1:
    if all_isos:
        iso_pie_df = get_top_n_for_pie(all_isos, 'ISO', 'Count', n=5)
        fig_iso_pie = px.pie(iso_pie_df, names='ISO', values='Count', title="ISO Distribution", hole=0.4)
        fig_iso_pie.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig_iso_pie, use_container_width=True)
    else:
        st.info("No ISO data.")

with exp_col2:
    if all_aps:
        ap_pie_df = get_top_n_for_pie(all_aps, 'Aperture', 'Count', n=5)
        fig_ap_pie = px.pie(ap_pie_df, names='Aperture', values='Count', title="Aperture Distribution", hole=0.4)
        fig_ap_pie.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig_ap_pie, use_container_width=True)
    else:
        st.info("No Aperture data.")

with exp_col3:
    if all_shutters:
        ss_pie_df = get_top_n_for_pie(all_shutters, 'Shutter Speed', 'Count', n=5)
        fig_ss_pie = px.pie(ss_pie_df, names='Shutter Speed', values='Count', title="Shutter Speed Distribution", hole=0.4)
        fig_ss_pie.update_layout(margin=dict(l=10, r=10, t=40, b=10))
        st.plotly_chart(fig_ss_pie, use_container_width=True)
    else:
        st.info("No Shutter Speed data.")

st.divider()

st.header("Content, Style & AI Tags")

meta_keywords = ['nikon', 'fujifilm', 'mm', 'iso', 'f/', 'ai-tagged', '0.0']
ai_tags = [t for t in all_tags_raw if not any(k in t.lower() for k in meta_keywords)]

if ai_tags:
    tag_df = pd.Series(ai_tags).value_counts().reset_index()
    tag_df.columns = ['Subject / Style Tag', 'Count']
    
    total_tags = len(tag_df)
    show_all_tags = False
    if total_tags > 10:
        c_tl, c_tc = st.columns([3, 1])
        with c_tl:
            st.markdown(f"**AI Subject Tags** (Showing Top 10 of {total_tags})")
        with c_tc:
            show_all_tags = st.checkbox("Expand All", key="expand_all_tags")
        plot_tag_df = tag_df if show_all_tags else tag_df.head(10)
    else:
        plot_tag_df = tag_df

    fig_tags = px.bar(
        plot_tag_df, x='Count', y='Subject / Style Tag', orientation='h', text='Count',
        title="AI-Generated Visual Tags & Subjects", color='Count', color_continuous_scale='Viridis'
    )
    fig_tags.update_traces(texttemplate='%{text}', textposition='outside')
    fig_tags.update_layout(
        height=max(400, min(len(plot_tag_df) * 25, 800)),
        yaxis={'categoryorder': 'total ascending'},
        margin=dict(l=25, r=40, t=40, b=20)
    )
    st.plotly_chart(fig_tags, use_container_width=True)

st.divider()

st.header("Recent Portfolio Updates")
st.dataframe(
    working_df[['photo_id', 'title', 'processed_at']].sort_values(by='processed_at', ascending=False),
    use_container_width=True,
    hide_index=True
)