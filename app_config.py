import os


RUNPOD_HOST  = os.environ.get("RUNPOD_HOST",  "213.173.108.11")
RUNPOD_PORT  = int(os.environ.get("RUNPOD_PORT", "12345"))
RUNPOD_USER  = os.environ.get("RUNPOD_USER",  "root")

# Authentication — set ONE of these:
SSH_KEY_PATH = os.environ.get("SSH_KEY_PATH", "~/.ssh/id_rsa")
SSH_PASSWORD = os.environ.get("SSH_PASSWORD", None) or None


if os.path.exists("/ssh/keys"):
    SSH_KEY_PATH = "/ssh/keys"


REMOTE_WORKSPACE      = "/workspace/cooler_labeling"
REMOTE_IMAGES_DIR     = "/workspace/cooler_labeling/images/uploaded"
REMOTE_OUTPUT_DIR     = "/workspace/cooler_labeling/output_pipeline_a"
REMOTE_PIPELINE       = "/workspace/cooler_labeling/pipeline_a.py"
REMOTE_VISUALIZATIONS = "/workspace/cooler_labeling/output_pipeline_a/visualizations"


if os.path.exists("/videos"):
    LOCAL_VIDEOS_DIR = "/videos"
    LOCAL_OUTPUT_DIR = "/output"
else:
    LOCAL_VIDEOS_DIR = os.environ.get("LOCAL_VIDEOS_DIR", "./videos")
    LOCAL_OUTPUT_DIR = os.environ.get("LOCAL_OUTPUT_DIR", "./pipeline_output")


FRAME_INTERVAL_SECONDS = float(os.environ.get("FRAME_INTERVAL_SECONDS", "1.0"))
MAX_FRAMES_PER_VIDEO   = int(os.environ.get("MAX_FRAMES_PER_VIDEO", "500"))


VIDEO_SOURCE     = os.environ.get("VIDEO_SOURCE", "local")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")
S3_BUCKET        = os.environ.get("S3_BUCKET", "")
S3_PREFIX        = os.environ.get("S3_PREFIX", "videos/")
S3_REGION        = os.environ.get("S3_REGION", "us-east-1")


PRODUCT_CLASS = os.environ.get("PRODUCT_CLASS", "coca_cola_original_taste")
