def parse_coordinate(coord_str: str, direction: str | None = None) -> float:
    """Convert coordinate string to float.

    Handles formats like:
    - '19.434900 N' (decimal degrees)
    - '33 deg 46\' 21.46" N' (degrees/minutes/seconds)
    """
    parts = coord_str.strip().split()

    # Check for DMS format (contains 'deg')
    if "deg" in coord_str.lower():
        # Parse format like "33 deg 46' 21.46" N"
        import re

        match = re.match(
            r"(\d+)\s*deg\s*(\d+)'\s*([\d.]+)\"?\s*([NSEW])?", coord_str, re.IGNORECASE
        )
        if match:
            degrees = float(match.group(1))
            minutes = float(match.group(2))
            seconds = float(match.group(3))
            dir_match = match.group(4)

            # Convert to decimal degrees
            value = degrees + (minutes / 60.0) + (seconds / 3600.0)

            # prefer direction passed in
            if direction and direction[0].upper() in ("S", "W"):
                value = -value
            elif dir_match and dir_match.upper() in ("S", "W"):
                value = -value

            return value

    # Decimal degrees format
    value = float(parts[0])

    # Check if it's South or West (negative)
    if len(parts) > 1 and parts[1].upper() in ("S", "W"):
        value = -value

    return value
