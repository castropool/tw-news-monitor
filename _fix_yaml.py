import yaml
from pathlib import Path

path = Path("config/sources.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8"))
sources = data["sources"] if isinstance(data, dict) else data

new_sources = [
    {"id": "cna_web_politics", "name": "中央社", "type": "cna_list_html", "category": "politics", "url": "https://www.cna.com.tw/list/aipl.aspx", "enabled": False},
    {"id": "cna_web_economy", "name": "中央社", "type": "cna_list_html", "category": "economy", "url": "https://www.cna.com.tw/list/aie.aspx", "enabled": False},
    {"id": "cna_web_international", "name": "中央社", "type": "cna_list_html", "category": "international", "url": "https://www.cna.com.tw/list/aopl.aspx", "enabled": False},
    {"id": "ltn_politics", "name": "自由時報", "type": "ltn_rss", "category": "politics", "url": "https://news.ltn.com.tw/rss/politics.xml", "enabled": True},
    {"id": "ltn_economy", "name": "自由時報", "type": "ltn_rss", "category": "economy", "url": "https://news.ltn.com.tw/rss/business.xml", "enabled": True},
    {"id": "ltn_international", "name": "自由時報", "type": "ltn_rss", "category": "international", "url": "https://news.ltn.com.tw/rss/world.xml", "enabled": True},
]

existing_ids = {s["id"] for s in sources if isinstance(s, dict)}
for ns in new_sources:
    if ns["id"] not in existing_ids:
        sources.append(ns)

output_data = {"sources": sources}
with open("config/sources.yaml", "w", encoding="utf-8") as f:
    yaml.dump(output_data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print("Updated sources.yaml:", len(sources), "sources")
for s in sources:
    if s["id"] in [ns["id"] for ns in new_sources]:
        e = s.get("enabled")
        print(f'  {s["id"]:25s} enabled={e}')