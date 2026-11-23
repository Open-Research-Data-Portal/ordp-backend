LEVEL_A_TYPES = {"csv", "tsv", "xlsx", "xls", "parquet", "json-table", "json_table"}
LEVEL_B1_TYPES = {"pdf", "docx", "txt", "md"}
LEVEL_B2_TYPES = {
    # Images
    "png", "jpeg", "jpg", "tiff", "tif", "webp", "gif", "bmp", "svg",
    # Audio
    "mp3", "wav", "flac", "ogg", "aac", "m4a",
    # Video
    "mp4", "mkv", "avi", "mov", "webm",
    # Geospatial / Scientific
    "geotiff", "netcdf", "hdf5", "dicom", "dcm", "nc", "h5", "hdf",
}


def get_level_for_type(file_type: str) -> str:
    """Return level A, B1, B2, or C according to file type."""
    cleaned = (file_type or "").strip().lower()
    if cleaned in LEVEL_A_TYPES:
        return "A"
    if cleaned in LEVEL_B1_TYPES:
        return "B1"
    if cleaned in LEVEL_B2_TYPES:
        return "B2"
    return "C"
