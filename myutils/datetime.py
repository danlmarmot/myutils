from datetime import datetime

# Month names because speed
MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


def parse_datetime(dt_str: str) -> datetime:
    for fmt in ["%Y:%m:%d %H:%M:%S%z", "%Y:%m:%d %H:%M:%S"]:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse: {dt_str}")
