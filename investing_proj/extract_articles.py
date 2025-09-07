import os
import sys
from typing import Dict, Any, List

# Make sure your package root is importable
project_root = os.path.abspath("/home/nim/venv/web_stuff/investing_proj")
sys.path.append(project_root)
print(sys.path)

from scrape_articles.news_fetchers import fetch_all_news

# Example: NVIDIA
res = fetch_all_news(
    "NVIDIA",
    tickers=["NVDA"],
    synonyms=["NVIDIA Corporation"],
    # extra_terms=["GPU", "AI"],
    # supply creds if you have them:
    gnews_token= "a0a597344c0c69d79f50ddb483743b7f",
    newsapi_key= "61b3e308bbf043789e83e53288f294be",
)


# What providers were attempted
print("Attempted:", res["attempted"])
print("Success flags:", res["ok"])
print("Errors:", res["errors"])

# Provider-specific full payloads
print("RSS count:", res["providers"]["google_news_rss"]["count"] if res["providers"]["google_news_rss"] else 0)
print("GNews count:", res["providers"]["gnews"]["count"] if res["providers"]["gnews"] else 0)
print("NewsAPI count:", res["providers"]["newsapi"]["count"] if res["providers"]["newsapi"] else 0)

# Merged view
print("Merged count:", res["merged"]["count"])
for i, it in enumerate(res["merged"]["items"][:5], 1):
    print(f"{i}. [{it['provider']}] {it['source']} — {it['title']} ({it['published']})")


# only gnews
gitems = [x for x in res["merged"]["items"] if x.get("provider") == "gnews"]
print("GNews (merged) count:", len(gitems))
for i, it in enumerate(gitems[:5], 1):
    print(f"{i}. [{it['provider']}] {it['source']} — {it['title']} ({it['published']})")

# only newsapi
newsapiitems = [x for x in res["merged"]["items"] if x.get("provider") == "newsapi"]
print("NewsAPI (merged) count:", len(newsapiitems))
for i, it in enumerate(newsapiitems[:5], 1):
    print(f"{i}. [{it['provider']}] {it['source']} — {it['title']} ({it['published']})")