import os
import glob
import shutil

# =========================================================
# USER CONFIGURATION
# List of file wildcards to delete in the root directory.
# Add or remove items here as needed.
# =========================================================
FILES_TO_DELETE = [
    '*.ffindex',
    '*.json',
    '*.csv',
    '*av1.mkv',
    '*.temp',
    '*.bsindex',
    '*.lwi',
]
# =========================================================

def cleanup_workspace():
    # Get the current working directory (root folder where .bat was run)
    cwd = os.getcwd()
    # Get the directory where this script is located (e.g., tools/)
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"Cleaning workspace in: {cwd}")

    # 1. Delete files matching the User Configuration list above (Root Directory)
    for pattern in FILES_TO_DELETE:
        # Create full path pattern (e.g., C:\Path\*.jpg)
        full_pattern = os.path.join(cwd, pattern)
        files = glob.glob(full_pattern)

        for file_path in files:
            try:
                os.remove(file_path)
                print(f"Deleted file: {os.path.basename(file_path)}")
            except OSError as e:
                print(f"Error deleting {os.path.basename(file_path)}: {e}")

    # 2. Delete 'logs' directory (Root)
    logs_dir = os.path.join(cwd, 'logs')
    if os.path.exists(logs_dir) and os.path.isdir(logs_dir):
        try:
            shutil.rmtree(logs_dir)
            print("Deleted folder: logs")
        except OSError as e:
            print(f"Error deleting logs folder: {e}")

    # 3. Delete Temp Folders based on naming convention
    # Looks for folders ending in "-source", "-source_scenedetect.scene-detection.tmp", ".tmp"
    # OR folders that start with a period (e.g., .temp, .cache)

    # Iterate over all items in the current directory
    for item in os.listdir(cwd):
        item_path = os.path.join(cwd, item)

        if os.path.isdir(item_path):
            # Check for the specific temp folder suffixes or prefix
            if (item.endswith("-source") or
                item.endswith("-source_scenedetect.scene-detection.tmp") or
                item.endswith(".tmp") or
                item.startswith(".")):

                try:
                    shutil.rmtree(item_path)
                    print(f"Deleted temp folder: {item}")
                except OSError as e:
                    print(f"Error deleting folder {item}: {e}")

    # 4. Delete ssimu2_bench_temp folder
    # Located in the same directory as this script (tools\ssimu2_bench_temp)
    ssimu2_temp_dir = os.path.join(script_dir, 'ssimu2_bench_temp')

    if os.path.exists(ssimu2_temp_dir) and os.path.isdir(ssimu2_temp_dir):
        try:
            shutil.rmtree(ssimu2_temp_dir)
            print(f"Deleted folder: {os.path.basename(ssimu2_temp_dir)}")
        except OSError as e:
            print(f"Error deleting {os.path.basename(ssimu2_temp_dir)}: {e}")

    # 5. Delete 'temp' folder with 'fastpass' exception logic
    # This corresponds to the dispatch.py temp directory
    temp_dir = os.path.join(cwd, 'temp')

    if os.path.exists(temp_dir) and os.path.isdir(temp_dir):
        # Check for any .mkv files inside
        mkv_files = glob.glob(os.path.join(temp_dir, "*.mkv"))

        # Filter files: We only want to preserve the folder if it contains "real" encodes.
        # If a file does NOT have "fastpass" in the name, it is considered important.
        files_to_preserve = [f for f in mkv_files if "fastpass" not in os.path.basename(f).lower()]

        # If there are no files to preserve (meaning empty, or only "fastpass" files exist), delete the folder.
        if not files_to_preserve:
            try:
                shutil.rmtree(temp_dir)
                print(f"Deleted folder: temp (Cleaned 'fastpass' or empty)")
            except OSError as e:
                print(f"Error deleting temp folder: {e}")
        else:
            print(f"Skipping deletion of 'temp' folder (contains {len(files_to_preserve)} essential .mkv files).")

    # 6. Delete *.ffindex from 'video-input' and 'filter' directories
    dirs_to_clean_ffindex = ['video-input', 'filter']

    for folder_name in dirs_to_clean_ffindex:
        folder_path = os.path.join(cwd, folder_name)

        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            ffindex_pattern = os.path.join(folder_path, '*.ffindex')
            files = glob.glob(ffindex_pattern)

            for file_path in files:
                try:
                    os.remove(file_path)
                    print(f"Deleted file from {folder_name}: {os.path.basename(file_path)}")
                except OSError as e:
                    print(f"Error deleting {os.path.basename(file_path)}: {e}")

    # 7. Specific Cleanup for 'video-input' (logs, *.bsindex, *.json, *.csv, dot folders)
    video_input_dir = os.path.join(cwd, 'video-input')

    if os.path.exists(video_input_dir) and os.path.isdir(video_input_dir):

        # A. Delete 'logs' folder inside video-input
        vi_logs_dir = os.path.join(video_input_dir, 'logs')
        if os.path.exists(vi_logs_dir) and os.path.isdir(vi_logs_dir):
            try:
                shutil.rmtree(vi_logs_dir)
                print(f"Deleted folder: {os.path.join('video-input', 'logs')}")
            except OSError as e:
                print(f"Error deleting video-input logs: {e}")

        # B. Delete *.bsindex, *.json and *.csv inside video-input
        vi_patterns = ['*.bsindex', '*.json', '*.csv']
        for pattern in vi_patterns:
            for file_path in glob.glob(os.path.join(video_input_dir, pattern)):
                try:
                    os.remove(file_path)
                    print(f"Deleted file from video-input: {os.path.basename(file_path)}")
                except OSError as e:
                    print(f"Error deleting {os.path.basename(file_path)}: {e}")

        # C. Delete folders starting with a period inside video-input
        # (e.g. .abc123 - the LQTC encoder's working/resume folder)
        for item in os.listdir(video_input_dir):
            item_path = os.path.join(video_input_dir, item)
            if item.startswith(".") and os.path.isdir(item_path):
                try:
                    shutil.rmtree(item_path)
                    print(f"Deleted temp folder from video-input: {item}")
                except OSError as e:
                    print(f"Error deleting folder {item}: {e}")

    # 8. Delete sample.mkv*.bsindex left next to this script (tools\)
    sample_bsindex_files = glob.glob(os.path.join(script_dir, 'sample.mkv*.bsindex'))
    for file_path in sample_bsindex_files:
        try:
            os.remove(file_path)
            print(f"Deleted file from tools: {os.path.basename(file_path)}")
        except OSError as e:
            print(f"Error deleting {os.path.basename(file_path)}: {e}")

if __name__ == "__main__":
    cleanup_workspace()