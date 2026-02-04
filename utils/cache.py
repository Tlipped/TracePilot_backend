import json
import os.path
import re
import time
from typing import Dict, Any

from settings import CACHE_DIR


def clean_name_for_matching(name):
    if not name:
        return ""
    name = name.lower()
    name = re.sub(r'[^a-z\d]', '', name)
    return name


def clear_cache(file_path: str):
    if not os.path.exists(file_path):
        print(f"The directory does not exist: {file_path}")
        return

    if not os.path.isdir(file_path):
        print(f"The path is not a directory.: {file_path}")
        return

    deleted_count = 0
    processed_count = 0
    files_to_delete = []

    for filename in os.listdir(file_path):
        file_full_path = os.path.join(file_path, filename)
        processed_count += 1

        try:
            with open(file_full_path, 'r', encoding='utf-8') as f:
                try:
                    json_data: Dict[str, Any] = json.load(f)
                    if isinstance(json_data, str):
                        print(f"JSON parsing failed and marked as deleted: {filename}")
                        print("Content of the document: " + json_data)
                        files_to_delete.append(file_full_path)
                    else:
                        print(f"Successfully parsed the JSON file: {filename}")
                except json.JSONDecodeError:
                    print(f"JSON parsing failed and marked as deleted: {filename}")
                    print("Content of the document: " + json_data)
                    files_to_delete.append(file_full_path)
        except IOError as e:
            print(f"Unable to read the file {filename}: {str(e)}")
        except Exception as e:
            print(f"Error occurred while processing file {filename}: {str(e)}")

    for file_path_to_delete in files_to_delete:
        success = delete_file_with_retry(file_path_to_delete, max_retries=3)
        if success:
            deleted_count += 1

    print(f"Cache cleaning completed: {processed_count} files were processed and {deleted_count} files were deleted.")


def delete_file_with_retry(file_path: str, max_retries: int = 3) -> bool:
    for attempt in range(max_retries):
        try:
            os.remove(file_path)
            print(f"Successfully deleted the file: {os.path.basename(file_path)}")
            return True
        except PermissionError as e:
            if attempt < max_retries - 1:
                print(f"File in use, retrying: {os.path.basename(file_path)} (Attempt {attempt + 1} of {max_retries})")
                time.sleep(0.5)
            else:
                print(f"Failed to delete file {os.path.basename(file_path)}: {str(e)}")
                return False
        except Exception as e:
            print(f"An error occurred while deleting the file {os.path.basename(file_path)}: {str(e)}")
            return False
    return False


if __name__ == '__main__':
    path = os.path.join(CACHE_DIR, "source")
    clear_cache(path)
