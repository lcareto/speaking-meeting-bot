import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

import requests
from loguru import logger


class UTFSUploader:
    def __init__(self, api_key: str, app_id: str):
        self.api_key = api_key
        self.app_id = app_id
        self.base_url = "https://api.uploadthing.com"  # Updated base URL
        self.personas_file = "config/personas.json"
        self.uploaded_urls = self._load_existing_urls()

        # Configure logger levels
        logger.remove()
        logger.add(lambda msg: print(msg), level="INFO")

    def _load_existing_urls(self) -> dict:
        """Load existing image URLs from personas.json"""
        try:
            with open(self.personas_file, "r") as f:
                personas = json.load(f)
                # Assuming personas is a list of dictionaries
                urls = {}
                for persona in personas:
                    if isinstance(persona, dict) and "key" in persona:
                        urls[persona["key"]] = persona.get("imageUrl")
                return urls
        except Exception as e:
            logger.error(f"Failed to load existing URLs: {e}")
            return {}

    def _image_needs_upload(self, persona_key: str) -> bool:
        """Check if image needs to be uploaded"""
        return (
            persona_key not in self.uploaded_urls or not self.uploaded_urls[persona_key]
        )

    def upload_file(
        self, file_path: Path, custom_id: Optional[str] = None
    ) -> Optional[str]:
        """Upload a file using the UploadThing API"""
        # Extract persona key from filename
        persona_key = Path(file_path).stem

        # Check if file exists locally
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        # Check if we need to upload
        if not self._image_needs_upload(persona_key):
            logger.info(f"Image already uploaded for {persona_key}")
            return self.uploaded_urls[persona_key]

        # Continue with existing upload logic
        logger.info(f"Uploading file: {file_path}")
        try:
            # Get file info
            file_name = file_path.name
            file_size = file_path.stat().st_size
            file_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

            # Step 1: Prepare the upload
            prepare_url = f"{self.base_url}/v6/uploadFiles"
            headers = {
                "x-uploadthing-api-key": self.api_key,
                "Content-Type": "application/json",
            }

            prepare_data = {
                "files": [{"name": file_name, "size": file_size, "type": file_type}],
                "acl": "public-read",
                "contentDisposition": "inline",
            }

            logger.info(f"Request data: {prepare_data}")
            logger.info(f"Uploading file: {file_name} (size: {file_size} bytes)")
            logger.info(f"Headers: {headers}")

            # Get presigned URL
            logger.info("Making request to get presigned URL...")
            response = requests.post(prepare_url, headers=headers, json=prepare_data)
            logger.info(f"Presigned URL response status: {response.status_code}")
            logger.debug(f"Presigned URL raw response: {response.text}")

            if response.status_code != 200:
                raise Exception(f"Failed to get presigned URL: {response.text}")

            presigned_data = response.json()
            logger.debug(f"Parsed presigned data: {presigned_data}")

            if not presigned_data.get("data"):
                raise Exception("No presigned URL received")

            file_data = presigned_data["data"][0]
            logger.info(f"Got presigned URL: {file_data['url']}")
            logger.debug(f"Upload fields: {file_data['fields']}")

            # Step 2: Upload to presigned URL
            logger.info("Starting file upload to presigned URL...")
            with open(file_path, "rb") as f:
                upload_response = requests.post(
                    file_data["url"],
                    data=file_data["fields"],
                    files={"file": (file_name, f, file_type)},
                    timeout=30,
                )
                logger.info(f"Upload response status: {upload_response.status_code}")
                logger.debug(f"Upload response: {upload_response.text}")

                if upload_response.status_code != 204:
                    raise Exception(f"Upload failed: {upload_response.text}")

            # Update personas.json with the new URL
            json_path = file_path.parent.parent / "personas.json"
            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        personas = json.load(f)

                    # Get base filename without extension
                    base_filename = (
                        file_path.stem
                    )  # This gets filename without extension
                    logger.debug(f"Looking for persona with key: {base_filename}")

                    # Check if persona exists directly by key
                    if base_filename in personas:
                        personas[base_filename]["image"] = file_data["fileUrl"]
                        logger.info(f"Updating image URL for {base_filename}")

                        # Save updated JSON
                        with open(json_path, "w", encoding="utf-8") as f:
                            json.dump(personas, f, indent=2, ensure_ascii=False)
                        logger.success(
                            f"Updated personas.json with new image URL for {base_filename}"
                        )
                    else:
                        logger.warning(
                            f"Could not find persona for key {base_filename}"
                        )
                        logger.debug(
                            "Available personas: " + ", ".join(personas.keys())
                        )

                except Exception as e:
                    logger.error(f"Error updating personas.json: {str(e)}")

            return file_data["fileUrl"]

        except Exception as e:
            logger.error(f"Error during upload: {str(e)}")
            return None

    def check_api_health(self) -> bool:
        """Check if the API is responding"""
        try:
            logger.info("Checking API health...")
            test_url = f"{self.base_url}/v6/uploadFiles"
            headers = {
                "x-uploadthing-api-key": self.api_key,
                "Content-Type": "application/json",
            }

            # Properly formatted test request based on API docs
            test_data = {
                "files": [{"name": "test.txt", "size": 100, "type": "text/plain"}],
                "acl": "public-read",
                "contentDisposition": "inline",
            }

            logger.debug(f"Making health check request with data: {test_data}")
            response = requests.post(
                test_url, headers=headers, json=test_data, timeout=10
            )

            logger.info(f"API health check response: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"API response: {response.text}")

            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error checking API health: {str(e)}")
            return False

    def verify_credentials(self) -> bool:
        """Verify API key and app ID are valid"""
        try:
            logger.info("Verifying credentials...")
            response = requests.post(
                f"{self.base_url}/v7/getAppInfo",
                headers={
                    "x-uploadthing-api-key": self.api_key,
                    "x-uploadthing-version": "7.0.0",
                    "Content-Type": "application/json",
                },
                timeout=5,
            )

            logger.info(f"Credentials check response: {response.status_code}")
            if response.status_code != 200:
                logger.error(
                    f"API returned status {response.status_code}: {response.text}"
                )
                return False

            app_info = response.json()
            logger.debug(f"App info: {app_info}")

            # Verify app ID matches
            if app_info.get("appId") != self.app_id:
                logger.error("App ID mismatch")
                return False

            return True

        except Exception as e:
            logger.error(f"Error verifying credentials: {str(e)}")
            return False


def create_parser() -> argparse.ArgumentParser:
    def verify_upload_endpoint(self) -> bool:
        """Verify if we can prepare an upload"""
        try:
            logger.info("Testing upload preparation...")
            prepare_url = "https://uploadthing.com/api/v7/prepareUpload"
            prepare_headers = {
                "x-uploadthing-api-key": self.api_key,
                "Content-Type": "application/json",
            }

            prepare_data = {
                "files": [{"name": "test.png", "size": 1024, "type": "image/png"}]
            }

            response = requests.post(
                prepare_url, headers=prepare_headers, json=prepare_data, timeout=10
            )

            logger.info(f"Upload preparation response: {response.status_code}")
            if response.status_code != 200:
                logger.error(f"Upload preparation failed: {response.text}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error testing upload endpoint: {str(e)}")
            return False


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for the image uploader"""
    parser = argparse.ArgumentParser(description="Upload files to UTFS.io")
    parser.add_argument("--api-key", required=True, help="UTFS API key")
    parser.add_argument("--app-id", required=True, help="UTFS App ID")
    parser.add_argument(
        "--file-path", type=Path, help="Path to a single file to upload"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Upload all images from local_images directory",
    )
    parser.add_argument(
        "--custom-id", help="Optional custom ID for the file", default=None
    )
    return parser


def main():
    """Main entry point for the image uploader"""
    parser = create_parser()
    args = parser.parse_args()

    if not args.batch and not args.file_path:
        parser.error("Either --file-path or --batch must be specified")

    try:
        uploader = UTFSUploader(api_key=args.api_key, app_id=args.app_id)

        # Check API health first
        if not uploader.check_api_health():
            logger.error("UploadThing API is not responding")
            return 1

        # Verify credentials
        if not uploader.verify_credentials():
            logger.error("Invalid API key or app ID")
            return 1

        if args.batch:
            # Batch upload from local_images directory
            local_images_dir = Path("./config/local_images")
            if not local_images_dir.exists() or not local_images_dir.is_dir():
                logger.error("local_images directory not found")
                return 1

            # Process each image file in the directory
            for image_file in local_images_dir.glob("*"):
                if not image_file.is_file():
                    continue

                if not mimetypes.guess_type(image_file)[0] or not mimetypes.guess_type(
                    image_file
                )[0].startswith("image/"):
                    logger.warning(f"Skipping non-image file: {image_file}")
                    continue

                logger.info(f"Processing file: {image_file}")
                file_url = uploader.upload_file(file_path=image_file)

                if not file_url:
                    logger.error(f"Failed to upload {image_file}")
                    return 1

                logger.success(f"Successfully uploaded: {image_file} -> {file_url}")

        else:
            # Single file upload
            file_url = uploader.upload_file(
                file_path=args.file_path, custom_id=args.custom_id
            )
            if not file_url:
                logger.error(f"Failed to upload {args.file_path}")
                return 1
            logger.success(f"Successfully uploaded: {args.file_path} -> {file_url}")

        return 0

    except Exception as e:
        logger.error(f"Error during upload: {str(e)}")
        return 1


if __name__ == "__main__":
    exit(main())
