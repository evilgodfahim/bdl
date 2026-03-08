import feedparser
import xml.etree.ElementTree as ET
import json
import os
from datetime import datetime, timezone, timedelta
import sys

# ===== CONFIG =====
FEEDS_FILE = "feeds.txt"
OUTPUT_FILE = "temp.xml"
LAST_SEEN_FILE = "last_seen_temp.json"
MAX_ARTICLE_AGE_HOURS = 24  # Keep only articles from last 24 hours
MAX_ITEMS = 5000  # Maximum number of articles in temp.xml

# ===== NAMESPACE REGISTRATION =====
# Must be done before any ET parsing/writing to preserve prefixes
NS = {
    "dc":      "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "atom":    "http://www.w3.org/2005/Atom",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

# Set UTF-8 encoding for output
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# ===== SOURCE DETECTION =====
def get_source(entry):
    """Identify news source from URL"""
    url = entry.get("link", "").lower()
    source_map = {
        "thedailystar": "The Daily Star",
        "prothomalo":   "Prothom Alo (English)",
        "dailysun":     "Daily Sun",
        "unb":          "UNB",
        "bss":          "BSS",
        "bangladeshpost": "Bangladesh Post",
        "observer":     "Observer",
        "dhakatribune": "Dhaka Tribune",
        "bdnews24":     "BDNEWS24",
        "newagebd":     "New Age",
        "tbsnews":      "The Business Standard",
        "financialexpress": "Financial Express",
    }
    for key, name in source_map.items():
        if key in url:
            return name
    return "Unknown"

# ===== DATE PARSING =====
def parse_date(entry):
    """
    Parse publication date from feed entry.
    Tries published_parsed → updated_parsed → now().
    Handles feeds that emit 'Invalid Date' (feedparser sets parsed tuple to None).
    """
    for field in ("published_parsed", "updated_parsed"):
        parsed = entry.get(field)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)

def is_recent(pub_date):
    """Check if article is within the 24-hour window"""
    return (datetime.now(timezone.utc) - pub_date) < timedelta(hours=MAX_ARTICLE_AGE_HOURS)

# ===== LAST SEEN MANAGEMENT =====
def load_last_seen():
    """Load URL tracking to prevent duplicates within 24 hours"""
    if os.path.exists(LAST_SEEN_FILE):
        with open(LAST_SEEN_FILE, "r") as f:
            data = json.load(f)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
        cleaned = {}
        for url, ts in data.items():
            try:
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt > cutoff:
                    cleaned[url] = ts
            except Exception:
                continue
        return cleaned
    return {}

def save_last_seen(data):
    """Save URL tracking"""
    with open(LAST_SEEN_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ===== XML MANAGEMENT =====
def load_existing_xml():
    """Load existing temp.xml or create new structure"""
    if os.path.exists(OUTPUT_FILE):
        tree = ET.parse(OUTPUT_FILE)
        root = tree.getroot()
        return tree, root

    # Build a fresh feed with namespace declarations on the root element
    root = ET.Element("rss", {
        "version":       "2.0",
        "xmlns:dc":      NS["dc"],
        "xmlns:content": NS["content"],
        "xmlns:atom":    NS["atom"],
    })
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text       = "Temporary News Collection"
    ET.SubElement(channel, "link").text        = "https://evilgodfahim.github.io/"
    ET.SubElement(channel, "description").text = "24-hour rolling news window"
    return ET.ElementTree(root), root

def clean_old_articles(root):
    """Remove articles older than 24 hours from XML"""
    channel = root.find("channel")
    if channel is None:
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_ARTICLE_AGE_HOURS)
    items_to_remove = []

    for item in channel.findall("item"):
        pub_date_str = item.findtext("pubDate", "")
        try:
            pub_date = datetime.strptime(pub_date_str, "%a, %d %b %Y %H:%M:%S GMT")
            pub_date = pub_date.replace(tzinfo=timezone.utc)
            if pub_date < cutoff:
                items_to_remove.append(item)
        except Exception:
            # Unparseable date → treat as old and remove
            items_to_remove.append(item)

    for item in items_to_remove:
        channel.remove(item)

    return len(items_to_remove)

def enforce_max_items(root):
    """Keep only the newest MAX_ITEMS articles"""
    channel = root.find("channel")
    if channel is None:
        return 0
    items = channel.findall("item")
    if len(items) <= MAX_ITEMS:
        return 0
    for item in items[MAX_ITEMS:]:
        channel.remove(item)
    return len(items) - MAX_ITEMS

# ===== MAIN COLLECTION LOGIC =====
def collect_articles():
    """Main function: collect new articles from all feeds"""

    if not os.path.exists(FEEDS_FILE):
        print(f"❌ {FEEDS_FILE} not found")
        return

    with open(FEEDS_FILE, "r") as f:
        feed_urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"📡 Fetching from {len(feed_urls)} feeds...")

    last_seen = load_last_seen()
    tree, root = load_existing_xml()

    removed_old = clean_old_articles(root)
    print(f"🗑️  Removed {removed_old} old articles (>24h)")

    new_articles = []
    feed_errors  = []

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries:
                title = entry.get("title", "").strip()

                # Use link; fall back to id (guid) if link is absent
                link = (entry.get("link") or entry.get("id") or "").strip()

                if not title or not link:
                    continue
                if link in last_seen:
                    continue

                pub_date = parse_date(entry)
                if not is_recent(pub_date):
                    continue

                source      = get_source(entry)
                description = entry.get("summary", "").strip()
                guid        = entry.get("id", link).strip()

                new_articles.append({
                    "title":       title,
                    "link":        link,
                    "guid":        guid,
                    "description": description,
                    "source":      source,
                    "pubDate":     pub_date,
                })
                last_seen[link] = pub_date.isoformat()

        except Exception as e:
            feed_errors.append(f"{feed_url}: {str(e)}")

    # Add new articles at the top of the channel
    channel   = root.find("channel")
    first_item = channel.find("item")

    for article in new_articles:
        item = ET.Element("item")
        ET.SubElement(item, "title").text   = article["title"]
        ET.SubElement(item, "link").text    = article["link"]

        guid_el = ET.SubElement(item, "guid")
        guid_el.text = article["guid"]
        guid_el.set("isPermaLink", "true")

        if article["description"]:
            ET.SubElement(item, "description").text = article["description"]

        ET.SubElement(item, "source").text  = article["source"]
        ET.SubElement(item, "pubDate").text = article["pubDate"].strftime("%a, %d %b %Y %H:%M:%S GMT")

        if first_item is not None:
            channel.insert(list(channel).index(first_item), item)
        else:
            channel.append(item)

    trimmed = enforce_max_items(root)
    if trimmed:
        print(f"📉 Trimmed {trimmed} oldest articles to enforce {MAX_ITEMS} limit")

    last_build = channel.find("lastBuildDate")
    if last_build is None:
        last_build = ET.SubElement(channel, "lastBuildDate")
    last_build.text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")

    tree.write(OUTPUT_FILE, encoding="utf-8", xml_declaration=True)
    save_last_seen(last_seen)

    total_items = len(channel.findall("item"))
    print(f"✅ Added {len(new_articles)} new articles")
    print(f"📦 Total in temp.xml: {total_items} articles")

    if feed_errors:
        print(f"⚠️  Feed errors ({len(feed_errors)}):")
        for error in feed_errors[:5]:
            print(f"   {error}")

    sys.exit(0)

if __name__ == "__main__":
    try:
        collect_articles()
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
