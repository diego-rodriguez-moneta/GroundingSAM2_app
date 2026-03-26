#!/usr/bin/env python3
# =============================================================================
# pipeline_app.py — Hybrid Local/RunPod Auto-Labeling Pipeline
#
# Usage:
#   python3 pipeline_app.py                  # full pipeline
#   python3 pipeline_app.py --extract-only   # only extract frames locally
#   python3 pipeline_app.py --remote-only    # skip extraction, run remote only
#   python3 pipeline_app.py --download-only  # skip everything, just download
# =============================================================================

import os
import sys
import time
import glob
import shutil
import argparse
import subprocess
from pathlib import Path

# --- Check dependencies ---
try:
    import cv2
except ImportError:
    print("ERROR: opencv-python not installed. Run: pip install opencv-python")
    sys.exit(1)

try:
    import paramiko
    from scp import SCPClient
except ImportError:
    print("ERROR: paramiko/scp not installed. Run: pip install paramiko scp")
    sys.exit(1)

from app_config import (
    RUNPOD_HOST, RUNPOD_PORT, RUNPOD_USER,
    SSH_KEY_PATH, SSH_PASSWORD,
    REMOTE_WORKSPACE, REMOTE_IMAGES_DIR,
    REMOTE_OUTPUT_DIR, REMOTE_PIPELINE,
    REMOTE_VISUALIZATIONS,
    LOCAL_VIDEOS_DIR, LOCAL_OUTPUT_DIR,
    FRAME_INTERVAL_SECONDS, MAX_FRAMES_PER_VIDEO,
    VIDEO_SOURCE, GDRIVE_FOLDER_ID,
    S3_BUCKET, S3_PREFIX, S3_REGION,
    PRODUCT_CLASS,
)

# =============================================================================
# Helpers
# =============================================================================

def print_step(step: int, total: int, msg: str):
    print(f"\n{'='*60}")
    print(f"  STEP {step}/{total} — {msg}")
    print(f"{'='*60}")

def print_info(msg: str):
    print(f"  ► {msg}")

def print_ok(msg: str):
    print(f"  ✓ {msg}")

def print_err(msg: str):
    print(f"  ✗ ERROR: {msg}")


# =============================================================================
# STEP 1 — Fetch videos from cloud storage (optional)
# =============================================================================

def fetch_videos_gdrive():
    """Download videos from a Google Drive folder."""
    try:
        import gdown
    except ImportError:
        print_err("gdown not installed. Run: pip install gdown")
        sys.exit(1)

    os.makedirs(LOCAL_VIDEOS_DIR, exist_ok=True)
    print_info(f"Downloading from Google Drive folder: {GDRIVE_FOLDER_ID}")
    url = f"https://drive.google.com/drive/folders/{GDRIVE_FOLDER_ID}"
    gdown.download_folder(url, output=LOCAL_VIDEOS_DIR, quiet=False)
    print_ok("Google Drive download complete.")


def fetch_videos_s3():
    """Download videos from an S3 bucket."""
    try:
        import boto3
    except ImportError:
        print_err("boto3 not installed. Run: pip install boto3")
        sys.exit(1)

    os.makedirs(LOCAL_VIDEOS_DIR, exist_ok=True)
    s3 = boto3.client("s3", region_name=S3_REGION)
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=S3_BUCKET, Prefix=S3_PREFIX)

    downloaded = 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                local_path = os.path.join(LOCAL_VIDEOS_DIR, Path(key).name)
                print_info(f"Downloading s3://{S3_BUCKET}/{key}")
                s3.download_file(S3_BUCKET, key, local_path)
                downloaded += 1

    print_ok(f"Downloaded {downloaded} videos from S3.")


# =============================================================================
# STEP 2 — Extract frames locally
# =============================================================================

def extract_frames(videos_dir: str, output_dir: str) -> int:
    """
    Extract frames from all videos in videos_dir.
    Returns total number of frames extracted.
    """
    video_extensions = [".mp4", ".mov", ".avi", ".mkv", ".webm"]
    videos = []
    for ext in video_extensions:
        videos.extend(glob.glob(os.path.join(videos_dir, f"*{ext}")))
        videos.extend(glob.glob(os.path.join(videos_dir, f"*{ext.upper()}")))

    if not videos:
        print_err(f"No videos found in '{videos_dir}'")
        print_info(f"Supported formats: {', '.join(video_extensions)}")
        sys.exit(1)

    print_info(f"Found {len(videos)} video(s) to process.")
    os.makedirs(output_dir, exist_ok=True)

    total_frames = 0

    for vid_idx, video_path in enumerate(videos, 1):
        video_name = Path(video_path).stem
        print_info(f"[{vid_idx}/{len(videos)}] Extracting frames from: {Path(video_path).name}")

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            print_err(f"Could not open video: {video_path}")
            continue

        fps        = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_vid  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        interval   = max(1, int(fps * FRAME_INTERVAL_SECONDS))
        duration_s = total_vid / fps

        print_info(f"    FPS: {fps:.1f} | Duration: {duration_s:.1f}s | "
                   f"Total frames: {total_vid} | Extracting every {interval} frames")

        frame_idx    = 0
        saved        = 0

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            if frame_idx % interval == 0 and saved < MAX_FRAMES_PER_VIDEO:
                out_name  = f"{video_name}_frame_{frame_idx:06d}.jpg"
                out_path  = os.path.join(output_dir, out_name)
                cv2.imwrite(out_path, frame)
                saved += 1

            frame_idx += 1

        cap.release()
        print_ok(f"    Extracted {saved} frames from {Path(video_path).name}")
        total_frames += saved

    print_ok(f"Total frames extracted: {total_frames}")
    return total_frames


# =============================================================================
# STEP 3 — SSH connection
# =============================================================================

def create_ssh_client() -> paramiko.SSHClient:
    """Create and return an authenticated SSH client."""
    print_info(f"Connecting to {RUNPOD_USER}@{RUNPOD_HOST}:{RUNPOD_PORT} ...")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = dict(
        hostname=RUNPOD_HOST,
        port=RUNPOD_PORT,
        username=RUNPOD_USER,
        timeout=30,
    )

    if SSH_KEY_PATH:
        key_path = os.path.expanduser(SSH_KEY_PATH)
        if os.path.exists(key_path):
            connect_kwargs["key_filename"] = key_path
        else:
            print_err(f"SSH key not found at: {key_path}")
            sys.exit(1)
    elif SSH_PASSWORD:
        connect_kwargs["password"] = SSH_PASSWORD
    else:
        print_err("No SSH key or password configured in app_config.py")
        sys.exit(1)

    client.connect(**connect_kwargs)
    print_ok("SSH connection established.")
    return client


def run_remote_command(client: paramiko.SSHClient, cmd: str, show_output: bool = True) -> str:
    """Run a command on the remote pod and return stdout."""
    stdin, stdout, stderr = client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()

    if show_output and out:
        for line in out.splitlines():
            print(f"    {line}")
    if err and exit_code != 0:
        print_err(f"Remote error: {err[:200]}")

    return out


# =============================================================================
# STEP 4 — Upload frames to RunPod
# =============================================================================

def upload_frames(client: paramiko.SSHClient, local_frames_dir: str):
    """Upload extracted frames to the RunPod instance."""
    frames = glob.glob(os.path.join(local_frames_dir, "*.jpg"))
    if not frames:
        print_err(f"No frames found in {local_frames_dir}")
        sys.exit(1)

    print_info(f"Uploading {len(frames)} frames to RunPod...")

    # Create remote directory
    run_remote_command(client, f"mkdir -p {REMOTE_IMAGES_DIR}", show_output=False)
    # Clear any previous frames
    run_remote_command(client, f"rm -f {REMOTE_IMAGES_DIR}/*.jpg", show_output=False)

    with SCPClient(client.get_transport(), progress=_scp_progress) as scp:
        scp.put(frames, remote_path=REMOTE_IMAGES_DIR)

    print_ok(f"Uploaded {len(frames)} frames.")


def _scp_progress(filename, size, sent):
    """Simple SCP progress indicator."""
    if size > 0:
        pct = int(sent / size * 100)
        if pct % 20 == 0 or sent == size:
            print(f"\r    Progress: {pct}%", end="", flush=True)
    if sent == size:
        print()


# =============================================================================
# STEP 5 — Run pipeline on RunPod
# =============================================================================

def run_remote_pipeline(client: paramiko.SSHClient):
    """Update config on pod and run pipeline_a.py."""
    print_info("Updating remote config to point to uploaded frames...")

    # Update the IMAGES_DIR in config.py to point to our uploaded folder
    run_remote_command(
        client,
        f"sed -i 's|IMAGES_DIR.*=.*|IMAGES_DIR = \"{REMOTE_IMAGES_DIR}\"|' "
        f"{REMOTE_WORKSPACE}/config.py",
        show_output=False
    )

    # Clear previous output
    run_remote_command(
        client,
        f"rm -rf {REMOTE_OUTPUT_DIR}",
        show_output=False
    )

    print_info("Running pipeline_a.py on RunPod (this may take a few minutes)...")
    print()

    # Run pipeline with live output
    transport = client.get_transport()
    channel   = transport.open_session()
    channel.exec_command(f"cd {REMOTE_WORKSPACE} && python3 pipeline_a.py")

    # Stream output live
    while True:
        if channel.recv_ready():
            data = channel.recv(1024).decode("utf-8", errors="replace")
            print(data, end="", flush=True)
        if channel.exit_status_ready():
            break
        time.sleep(0.1)

    exit_code = channel.recv_exit_status()
    if exit_code != 0:
        print_err(f"Pipeline exited with code {exit_code}")
        sys.exit(1)

    print_ok("Remote pipeline complete.")


# =============================================================================
# STEP 6 — Generate visualizations on RunPod
# =============================================================================

def generate_remote_visualizations(client: paramiko.SSHClient):
    """Run visualization script on the pod."""
    print_info("Generating visualizations on RunPod...")

    viz_script = f"""
import os, cv2, glob
from pathlib import Path

images_dir  = '{REMOTE_IMAGES_DIR}'
labels_dir  = '{REMOTE_OUTPUT_DIR}/labels'
output_dir  = '{REMOTE_VISUALIZATIONS}'
os.makedirs(output_dir, exist_ok=True)

images = glob.glob(os.path.join(images_dir, '*.jpg'))
for img_path in images:
    stem       = Path(img_path).stem
    label_path = os.path.join(labels_dir, f'{{stem}}.txt')
    img = cv2.imread(img_path)
    if img is None:
        continue
    h, w = img.shape[:2]
    if os.path.exists(label_path):
        with open(label_path) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) == 5:
                    _, cx, cy, bw, bh = map(float, parts)
                    x1 = int((cx - bw/2) * w)
                    y1 = int((cy - bh/2) * h)
                    x2 = int((cx + bw/2) * w)
                    y2 = int((cy + bh/2) * h)
                    cv2.rectangle(img, (x1,y1),(x2,y2),(0,255,0),2)
                    cv2.putText(img, '{PRODUCT_CLASS}', (x1, y1-8),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,255,0), 2)
    cv2.imwrite(os.path.join(output_dir, f'{{stem}}.jpg'), img)
print('Visualizations done.')
"""

    run_remote_command(
        client,
        f"python3 -c \"{viz_script.strip()}\"",
        show_output=True
    )
    print_ok("Visualizations generated.")


# =============================================================================
# STEP 7 — Download and organize results
# =============================================================================

def download_and_organize(client: paramiko.SSHClient, local_output: str):
    """Download results and organize into detected/not_detected folders."""

    # Local folder structure
    detected_images = os.path.join(local_output, "detected", "images")
    detected_labels = os.path.join(local_output, "detected", "labels")
    detected_viz    = os.path.join(local_output, "detected", "visualizations")
    not_detected    = os.path.join(local_output, "not_detected", "images")

    for d in [detected_images, detected_labels, detected_viz, not_detected]:
        os.makedirs(d, exist_ok=True)

    # Create temp download dir
    tmp_dir = os.path.join(local_output, "_tmp_download")
    os.makedirs(tmp_dir, exist_ok=True)

    print_info("Downloading labels from RunPod...")
    with SCPClient(client.get_transport()) as scp:
        scp.get(f"{REMOTE_OUTPUT_DIR}/labels", local_path=tmp_dir, recursive=True)

    print_info("Downloading images from RunPod...")
    with SCPClient(client.get_transport()) as scp:
        scp.get(REMOTE_IMAGES_DIR, local_path=tmp_dir, recursive=True)

    print_info("Downloading visualizations from RunPod...")
    try:
        with SCPClient(client.get_transport()) as scp:
            scp.get(REMOTE_VISUALIZATIONS, local_path=tmp_dir, recursive=True)
    except Exception:
        print_info("No visualizations found — skipping.")

    # Organize by detection result
    print_info("Organizing results...")

    labels_tmp = os.path.join(tmp_dir, "labels")
    images_tmp = os.path.join(tmp_dir, "uploaded")
    viz_tmp    = os.path.join(tmp_dir, "visualizations")

    label_files = glob.glob(os.path.join(labels_tmp, "*.txt"))
    detected_count    = 0
    not_detected_count = 0

    for label_path in label_files:
        stem = Path(label_path).stem

        # Check if label file has any detections
        has_detection = os.path.getsize(label_path) > 0

        # Source paths
        img_src = os.path.join(images_tmp, f"{stem}.jpg")
        viz_src = os.path.join(viz_tmp,    f"{stem}.jpg")

        if has_detection:
            # Copy image, label, and visualization to detected/
            if os.path.exists(img_src):
                shutil.copy2(img_src, os.path.join(detected_images, f"{stem}.jpg"))
            shutil.copy2(label_path, os.path.join(detected_labels, f"{stem}.txt"))
            if os.path.exists(viz_src):
                shutil.copy2(viz_src, os.path.join(detected_viz, f"{stem}.jpg"))
            detected_count += 1
        else:
            # Copy only image to not_detected/
            if os.path.exists(img_src):
                shutil.copy2(img_src, os.path.join(not_detected, f"{stem}.jpg"))
            not_detected_count += 1

    # Cleanup temp
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print_ok(f"Organized: {detected_count} detected, {not_detected_count} not detected")
    return detected_count, not_detected_count


# =============================================================================
# MAIN
# =============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Hybrid Local/RunPod Auto-Labeling Pipeline"
    )
    parser.add_argument("--extract-only",  action="store_true",
                        help="Only extract frames locally, don't connect to RunPod")
    parser.add_argument("--remote-only",   action="store_true",
                        help="Skip extraction, upload existing frames and run remote")
    parser.add_argument("--download-only", action="store_true",
                        help="Skip everything, just download and organize results")
    return parser.parse_args()


def main():
    args  = parse_args()
    STEPS = 7 if not any([args.extract_only, args.remote_only, args.download_only]) else 3

    print("\n" + "="*60)
    print("  Smart Cooler — Hybrid Auto-Labeling Pipeline")
    print("="*60)

    local_frames_dir = os.path.join(LOCAL_OUTPUT_DIR, "_frames")

    # ── Step 1: Fetch videos from cloud (if needed) ──────────────────────────
    if not args.remote_only and not args.download_only:
        if VIDEO_SOURCE == "gdrive":
            print_step(1, STEPS, "Fetching videos from Google Drive")
            fetch_videos_gdrive()
        elif VIDEO_SOURCE == "s3":
            print_step(1, STEPS, "Fetching videos from S3")
            fetch_videos_s3()
        else:
            print_step(1, STEPS, f"Using local videos from '{LOCAL_VIDEOS_DIR}'")
            if not os.path.exists(LOCAL_VIDEOS_DIR):
                print_err(f"Videos directory not found: '{LOCAL_VIDEOS_DIR}'")
                sys.exit(1)
            print_ok(f"Local video source confirmed.")

    # ── Step 2: Extract frames ────────────────────────────────────────────────
    if not args.remote_only and not args.download_only:
        print_step(2, STEPS, "Extracting frames from videos")
        n_frames = extract_frames(LOCAL_VIDEOS_DIR, local_frames_dir)
        print_ok(f"{n_frames} frames ready in: {local_frames_dir}")

        if args.extract_only:
            print("\n  Extract-only mode complete.")
            print(f"  Frames saved to: {local_frames_dir}")
            return

    # ── Step 3: Connect to RunPod ─────────────────────────────────────────────
    print_step(3, STEPS, "Connecting to RunPod")
    ssh = create_ssh_client()

    if args.download_only:
        print_step(4, STEPS, "Downloading and organizing results")
        d, nd = download_and_organize(ssh, LOCAL_OUTPUT_DIR)
    else:
        # ── Step 4: Upload frames ─────────────────────────────────────────────
        print_step(4, STEPS, "Uploading frames to RunPod")
        upload_frames(ssh, local_frames_dir)

        # ── Step 5: Run pipeline ──────────────────────────────────────────────
        print_step(5, STEPS, "Running pipeline on RunPod")
        run_remote_pipeline(ssh)

        # ── Step 6: Generate visualizations ──────────────────────────────────
        print_step(6, STEPS, "Generating visualizations on RunPod")
        generate_remote_visualizations(ssh)

        # ── Step 7: Download and organize ────────────────────────────────────
        print_step(7, STEPS, "Downloading and organizing results")
        d, nd = download_and_organize(ssh, LOCAL_OUTPUT_DIR)

    ssh.close()

    # ── Final summary ─────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  Pipeline Complete!")
    print("="*60)
    print(f"  Output folder   : {LOCAL_OUTPUT_DIR}/")
    print(f"  Detected        : {d} images  → detected/")
    print(f"  Not detected    : {nd} images → not_detected/")
    print(f"\n  Output structure:")
    print(f"    {LOCAL_OUTPUT_DIR}/")
    print(f"    ├── detected/")
    print(f"    │   ├── images/         ← frames with product")
    print(f"    │   ├── labels/         ← YOLO label files")
    print(f"    │   └── visualizations/ ← images with boxes drawn")
    print(f"    └── not_detected/")
    print(f"        └── images/         ← frames without product")
    print("="*60)


if __name__ == "__main__":
    main()
