import re
from telegram.constants import ParseMode

def generate_custom_caption(original_caption: str, channel_username: str) -> str:
    # Extract title and year
    title_match = re.match(r"^(.*?)\s*\((\d{4})\)?", original_caption)
    name = title_match.group(1).strip() if title_match else "Movie"
    year = title_match.group(2) if title_match else ""

    # Lowercase caption for easier matching
    caption_lower = original_caption.lower()

    # Extract possible languages (add more variations as needed)
    known_languages = {
        "malayalam": "#Malayalam",
        "hindi": "#Hindi",
        "hin": "#Hindi",
        "tamil": "#Tamil",
        "telugu": "#Telugu",
        "kan": "#Kannada",
        "kannada": "#Kannada",
        "english": "#English",
        "eng": "#English"
    }

    found_langs = []
    for key, tag in known_languages.items():
        if key in caption_lower and tag not in found_langs:
            found_langs.append(tag)

    language_str = " ".join(found_langs) if found_langs else ""

    # Extract quality terms
    quality_keywords = re.findall(r"(2160p|4k|1440p|1080p|720p|480p|360p|hdr|webrip|web-dl|bluray|nf|uhd|10bit|hevc|x265|x264|ddp5\.1|esub)", caption_lower, re.IGNORECASE)
    quality_str = " ".join(dict.fromkeys([q.upper() for q in quality_keywords]))  # remove duplicates, preserve order

    # Extract size
    size_match = re.search(r"(\d+(?:\.\d+)?\s*(GB|MB))", original_caption, re.IGNORECASE)
    size_str = size_match.group(0).replace(" ", "") if size_match else ""

    # Compose final caption
    title_line = f"*{name} ({year})*" if year else f"*{name}*"
    info_line = f"{language_str} {quality_str} {size_str}".strip()
    footer = f"\n🔗 @{channel_username.lstrip('@')}"

    return f"{title_line}\n{info_line}\n{footer}"
