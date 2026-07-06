import subprocess
import shutil
import sys
import json
from pathlib import Path
from datetime import datetime

from myutils.gps import parse_coordinate

# Check if exiftool is available
EXIFTOOL = shutil.which("exiftool")
if not EXIFTOOL:
    print("Error: exiftool not found. Install exiftool and try again.", file=sys.stderr)
    sys.exit(1)


def image_get_coordinates(exif_data) -> tuple[float, float]:
    lat, long = 0, 0

    exif_lat = (
        exif_data.get("EXIF:GPSLatitude")  # iPhone 16 sets this
        or exif_data.get("EXIF:GPSInfo:GPSLatitude")
        or exif_data.get("EXIF:Composite:GPSLatitude")  # iPhone 16 HEIC sets this
        or exif_data.get("Composite:GPSLatitude")  # iPhone 16 MOV sets this
        or 0
    )

    if exif_lat != 0:
        lat = parse_coordinate(exif_lat)

    exif_long = (
        exif_data.get("EXIF:GPSLongitude")  # iPhone 16 sets this
        or exif_data.get("EXIF:GPSInfo:GPSLongitude")
        or exif_data.get("EXIF:Composite:GPSLongitude")  # iPhone 16 HEIC sets this
        or exif_data.get("Composite:GPSLongitude")  # iPhone 16 MOV sets this
        or 0
    )

    if exif_long != 0:
        long = parse_coordinate(exif_long)

    return lat, long


def image_set_original_filename(image_path: Path, original_filename: str) -> None:
    """Set the original filename in EXIF metadata."""
    subprocess.run(
        [
            EXIFTOOL,
            "-overwrite_original",
            f"-OriginalFileName={original_filename}",
            str(image_path),
        ],
        check=True,
    )


def get_exif_data(image_file: Path, success: bool = True, timeout: int = 1) -> dict:
    # exiftool usage:
    # exiftool -a -G1 -s -n -struct /path/to/image.jpg
    # -a - Allow duplicate tags (extract all)
    # -G1 - Show group names (e.g., EXIF, GPS, XMP)
    # -s - Short output format (tag names only)
    # -n - Don't convert tags to native format (e.g., convert DateTimeOriginal to ISO 8601), use raw numeric values
    # -struct - Show the full structure of the EXIF data (e.g., show the GPS sub-IFD)

    # for JSON output, use -j
    # exiftool -j -a -G -struct /path/to/image.jpg

    # add -b for binary data like thumbnails (not done here, not a masochist)

    try:
        result = subprocess.run(
            [
                EXIFTOOL,
                "-j",
                "-a",
                "-G",
                "-struct",
                image_file,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:
        raise
    except Exception as e:
        print(e)
        sys.exit(1)

    if result.returncode != 0:
        print(f"Error running exiftool on {image_file}: {result.stderr}")
        return {}, False

    exif_data = json.loads(result.stdout)[0]
    return exif_data, success


def exif_get_file_type(exif_data: dict) -> str:
    file_type = exif_data.get("File:FileType") or "unknown"

    if file_type == "unknown":
        print("Warning: Could not determine file type.")

    return file_type


def exif_get_dimensions(exif_data: dict) -> tuple[int, int] | None:
    width, height = (
        int(
            exif_data.get("File:ImageWidth")  # iPhone 16 Pro
            or exif_data.get("QuickTime:ImageWidth")  # iPhone 16 Pro
            or exif_data.get("EXIF:ImageWidth")  # Sony-RX100M6
            or exif_data.get("EXIF:ExifImageWidth")  # PNG screenshots with EXIF
            or exif_data.get("PNG:ImageWidth")  # PNG without EXIF block
            or 0
        ),
        int(
            exif_data.get("File:ImageHeight")
            or exif_data.get("QuickTime:ImageHeight")
            or exif_data.get("EXIF:ImageHeight")
            or exif_data.get("EXIF:ExifImageHeight")
            or exif_data.get("PNG:ImageHeight")  # PNG without EXIF block
            or 0
        ),
    )

    if not width or not height:
        return None

    return width, height


def image_has_capture_one_color_label(
    image_file: Path, color: str | None = None, timeout: int = 1
) -> bool:
    """Return True if the image has a color label set by CaptureOne.

    CaptureOne stores color labels in XMP:Label as strings like
    'Red', 'Yellow', 'Green', 'Blue', or 'Purple'.

    If color is provided, returns True only if the label matches that color
    (case-insensitive). If color is None, returns True if any label is set.
    """
    exif_data, success = get_exif_data(image_file, timeout=timeout)
    if not success:
        return False

    label = (exif_data.get("XMP:Label") or "").strip()
    if not label:
        return False

    if color is None:
        return True

    return label.lower() == color.lower()


def exif_set_datetime(image_file: Path, dt: datetime, timeout: int = 2) -> bool:
    """Set DateTimeOriginal on a given image file using exiftool."""

    try:
        dt_str = dt.strftime("%Y:%m:%d %H:%M:%S")

        # Subseconds (microseconds as decimal)
        # Convert microseconds to fractional seconds (e.g., 123456 -> "123456")
        subsec_str = f"{dt.microsecond:06d}".rstrip("0") or "0"

        # Timezone offset: "+HH:MM" or "-HH:MM"
        if dt.tzinfo is not None:
            offset = dt.strftime("%z")  # e.g., "+0200" or "-0500"
            # Format as "+02:00" or "-05:00"
            tz_str = f"{offset[:3]}:{offset[3:]}"
        else:
            tz_str = None

        # Build exiftool command
        cmd = [
            EXIFTOOL,
            f"-DateTimeOriginal={dt_str}",
            f"-SubSecTimeOriginal={subsec_str}",
        ]

        if tz_str:
            cmd.append(f"-OffsetTimeOriginal={tz_str}")

        cmd.extend(["-overwrite_original", str(image_file)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

        if result.returncode == 0:
            print(
                f"Set EXIF date on {image_file}: {dt_str}.{subsec_str}{tz_str if tz_str else ''}"
            )
            return True
        else:
            print(
                f"Error setting EXIF on {image_file}: {result.stderr}", file=sys.stderr
            )
            return False

    except Exception as e:
        print(f"Error setting EXIF datetime: {e}", file=sys.stderr)
        sys.exit(1)

    return False
