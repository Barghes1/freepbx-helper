from urllib.parse import urlparse

def clean_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc or url.replace("http://", "").replace("https://", "")
