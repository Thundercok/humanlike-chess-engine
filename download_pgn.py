import os
import requests
from tqdm import tqdm

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

URL = "https://database.lichess.org/standard/lichess_db_standard_rated_2014-01.pgn.zst"
DEST_PATH = os.path.join(DATA_DIR, "lichess_2014_01.pgn.zst")

def download_file(url, dest_path, chunk_size=1024*1024):
    response = requests.get(url, stream=True, timeout=30)
    total = int(response.headers.get('content-length', 0))
    with open(dest_path, 'wb') as f, tqdm(
            desc=os.path.basename(dest_path),
            total=total // chunk_size,
            unit='MiB',
            unit_scale=True,
            unit_divisor=1024,
    ) as bar:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                bar.update(1)

if __name__ == "__main__":
    if not os.path.isfile(DEST_PATH):
        print(f"Downloading {URL} -> {DEST_PATH}")
        download_file(URL, DEST_PATH)
        print("Download complete.")
    else:
        print(f"File already exists at {DEST_PATH}. Skipping download.")
