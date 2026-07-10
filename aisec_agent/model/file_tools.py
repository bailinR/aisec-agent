import logging
import io
from collections.abc import Iterator
from typing import BinaryIO, Dict, Any
import fitz

logger = logging.getLogger(__name__)


class PDFExtractor:

    def __init__(self, file: BinaryIO):
        self._file = file

    def extract(self) -> list[dict]:
        documents_data = []
        original_position = self._file.tell()
        try:
            self._file.seek(0)
            file_content = io.BytesIO(self._file.read())

            for page_data in self.parse(file_content):
                documents_data.append(page_data)
        except Exception as e:
            logger.error(f"Error during PDF extraction with PyMuPDF: {e}", exc_info=True)
            raise
        finally:
            self._file.seek(original_position)
        return documents_data

    def parse(self, file_obj: BinaryIO) -> Iterator[Dict[str, Any]]:
        try:
            with fitz.open(stream=file_obj, filetype="pdf") as document:
                doc_metadata = document.metadata or {}
                if doc_metadata:
                    logger.debug(f"PDF Document Metadata: {doc_metadata}")

                for page_number, page in enumerate(document):
                    page_data: Dict[str, Any] = {
                        "page": page_number,
                        "text_content": "",
                        "images": []
                    }

                    try:
                        page_data["text_content"] = page.get_text()
                    except Exception as e:
                        logger.warning(f"Could not extract text from page {page_number}: {e}")
                        page_data["text_content"] = ""

                    try:
                        image_list = page.get_images(full=True)

                        for img_index, img_info in enumerate(image_list):
                            xref = img_info[0]  # The cross-reference number for the image
                            img_dict_raw = document.extract_image(xref)
                            if img_dict_raw and img_dict_raw["image"]:
                                file_extension = img_dict_raw["ext"].lower()
                                mime_type_map = {
                                    "png": "image/png",
                                    "jpeg": "image/jpeg",
                                    "jpg": "image/jpeg",
                                    "gif": "image/gif",
                                    "webp": "image/webp",
                                    "jp2": "image/jp2",
                                    "jpx": "image/jpx",
                                    "bmp": "image/bmp",
                                    "tiff": "image/tiff",
                                    "tif": "image/tiff",
                                }
                                mime_type = mime_type_map.get(file_extension, "application/octet-stream")
                                formatted_image_dict = {
                                    "content": img_dict_raw["image"],
                                    "mime_type": mime_type,
                                    "extension": file_extension,
                                    "page_number": page_number,  # Add page_number for context
                                    "image_index": img_index  # Add image_index for uniqueness
                                }
                                page_data["images"].append(formatted_image_dict)
                    except Exception as e:
                        logger.warning(f"Error extracting images from page {page_number}: {e}", exc_info=True)

                    yield page_data

        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing the PDF document with PyMuPDF: {e}",
                         exc_info=True)
            raise