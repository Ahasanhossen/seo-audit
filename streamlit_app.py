import streamlit as st
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from collections import deque
import time
import re
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="SEO Audit Tool", page_icon="🔍", layout="wide")

USER_AGENT = "Mozilla/5.0 (compatible; StreamlitSEOAuditBot/1.0; +https://github.com/seo-audit)"

def normalize_url(url):
    parsed = urlparse(url)
    normalized = parsed._replace(fragment="").geturl()
    if normalized.endswith("/") and urlparse(normalized).path != "/":
        normalized = normalized.rstrip("/")
    return normalized

def same_domain(url, base_domain):
    host = urlparse(url).netloc.lower()
    base = base_domain.lower()
    return host == base or host.endswith("." + base)

def is_crawlable(url):
    skip_exts = (
        ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".pdf", ".zip", ".gz", ".tar", ".mp4", ".mp3", ".avi",
        ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
        ".xml", ".json", ".txt"
    )
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in skip_exts)

# ──────────────────────────────────────────────────────────────────────────────
# HTTP FETCHER & ANALYZER
# ──────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_session():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session

def fetch(url, method="GET", timeout=10, retries=1):
    session = get_session()
    result = {
        "status_code": None, "final_url": url, "content_type": "",
        "html": "", "response_time_ms": 0, "error": None, "redirect_chain": []
    }
    for attempt in range(retries + 1):
        try:
            start = time.time()
            resp = session.request(method, url, timeout=timeout, allow_redirects=True)
            elapsed = round((time.time() - start) * 1000)
            chain = [r.url for r in resp.history]
            result.update({
                "status_code": resp.status_code, "final_url": resp.url,
                "content_type": resp.headers.get("Content-Type", ""),
                "response_time_ms": elapsed, "redirect_chain": chain,
            })
            if "text/html" in result["content_type"]:
                result["html"] = resp.text
            return result
        except Exception as e:
            result["error"] = str(e)[:100]
        if attempt < retries: time.sleep(1)
    return result

def analyze_page(url, html, fetch_result):
    soup = BeautifulSoup(html, "lxml")
    issues, warnings_list, good = [], [], []

    # Title
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    title_len = len(title_text)
    if not title_text: issues.append("Missing <title> tag")
    elif title_len < 30: warnings_list.append(f"Title too short ({title_len} chars)")
    elif title_len > 65: warnings_list.append(f"Title too long ({title_len} chars)")
    else: good.append("Title length OK")

    # Meta Description
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    meta_desc = meta_desc_tag["content"].strip() if meta_desc_tag and meta_desc_tag.get("content") else ""
    if not meta_desc: issues.append("Missing meta description")
    elif len(meta_desc) < 70: warnings_list.append("Meta description too short")
    else: good.append("Meta description OK")

    # H1
    h1_tags = soup.find_all("h1")
    if len(h1_tags) == 0: issues.append("No H1 tag found")
    elif len(h1_tags) > 1: warnings_list.append(f"Multiple H1 tags ({len(h1_tags)})")

    # Images
    images = soup.find_all("img")
    imgs_no_alt = [img.get("src","")[:40] for img in images if not img.get("alt")]
    if imgs_no_alt: issues.append(f"{len(imgs_no_alt)} image(s) missing alt text")

    # HTTPS & Speed
    if not url.startswith("https://"): issues.append("Not HTTPS")
    if fetch_result["response_time_ms"] > 2000: warnings_list.append("Slow response time (>2s)")

    # Links extraction
    links = []
    for a in soup.find_all("a", href=True):
        full_url = urljoin(url, a["href"].strip())
        if urlparse(full_url).scheme in ("http", "https"):
            links.append({"url": full_url, "text": a.get_text(strip=True)[:40]})

    return {
        "URL": url,
        "Status": fetch_result["status_code"],
        "Title": title_text[:50],
        "Words": len(soup.get_text(separator=" ", strip=True).split()),
        "Load Time (ms)": fetch_result["response_time_ms"],
        "Issues Count": len(issues),
        "Warnings Count": len(warnings_list),
        "_issues": issues,
        "_warnings": warnings_list,
        "_links": links
    }

# ──────────────────────────────────────────────────────────────────────────────
# CRAWL LOGIC
# ──────────────────────────────────────────────────────────────────────────────

def run_audit(base_url, max_pages, check_ext):
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc
    queue = deque([normalize_url(base_url)])
    visited = set()
    page_results = []
    all_links_map = {}

    progress_bar = st.progress(0)
    status_text = st.empty()

    pages_crawled = 0
    while queue and pages_crawled < max_pages:
        url = queue.popleft()
        if url in visited or not is_crawlable(url): continue
        visited.add(url)
        
        status_text.text(f"Crawling: {url}")
        
        fetch_res = fetch(url)
        if fetch_res["status_code"] and fetch_res["html"]:
            data = analyze_page(url, fetch_res["html"], fetch_res)
            page_results.append(data)
            
            for link in data["_links"]:
                norm = normalize_url(link["url"])
                is_int = same_domain(norm, base_domain)
                if is_int and norm not in visited:
                    queue.append(norm)
                
                if norm not in all_links_map:
                    all_links_map[norm] = {"url": norm, "is_internal": is_int, "sources": []}
                all_links_map[norm]["sources"].append(url)
                
        pages_crawled += 1
        progress_bar.progress(pages_crawled / max_pages)
        time.sleep(0.1) # Polite delay

    # Link Checking
    status_text.text("Checking links for broken statuses...")
    checked_links = []
    links_to_check = list(all_links_map.values())
    
    for i, link in enumerate(links_to_check):
        if not check_ext and not link["is_internal"]:
            link["status"] = "Skipped"
        else:
            res = fetch(link["url"], method="HEAD")
            if res["status_code"] is None or res["status_code"] >= 400:
                res = fetch(link["url"], method="GET") # Fallback to GET
            link["status"] = res["status_code"] or "Error"
        
        checked_links.append({
            "URL": link["url"],
            "Status": link["status"],
            "Type": "Internal" if link["is_internal"] else "External",
            "Found On": link["sources"][0]
        })

    status_text.empty()
    progress_bar.empty()
    return page_results, checked_links

# ──────────────────────────────────────────────────────────────────────────────
# STREAMLIT UI
# ──────────────────────────────────────────────────────────────────────────────

st.title("🔍 Live Web SEO Audit")
st.markdown("Enter a website URL to crawl its pages, check on-page SEO signals, and find broken links.")

with st.sidebar:
    st.header("Settings")
    target_url = st.text_input("Target URL", value="https://example.com")
    max_pages = st.slider("Max Pages to Crawl", 1, 200, 20)
    check_ext_links = st.checkbox("Check External Links", value=True)
    start_btn = st.button("🚀 Run Audit", type="primary", use_container_width=True)

if start_btn:
    if not target_url.startswith("http"):
        st.error("Please enter a valid URL starting with http:// or https://")
    else:
        with st.spinner("Audit in progress..."):
            pages_data, links_data = run_audit(target_url, max_pages, check_ext_links)
        
        if not pages_data:
            st.error("Could not crawl the website. Check the URL or server restrictions.")
        else:
            # Metrics
            st.success("Audit Complete!")
            total_issues = sum(p["Issues Count"] for p in pages_data)
            broken_links = sum(1 for l in links_data if isinstance(l["Status"], int) and l["Status"] >= 400)
            
            score = max(0, 100 - (total_issues * 5) - (broken_links * 2))
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Health Score", f"{score}/100", delta_color="off")
            col2.metric("Pages Crawled", len(pages_data))
            col3.metric("Critical Issues", total_issues, delta=-total_issues if total_issues>0 else None, delta_color="inverse")
            col4.metric("Broken Links", broken_links, delta=-broken_links if broken_links>0 else None, delta_color="inverse")
            
            # Tabs for detailed data
            tab1, tab2, tab3 = st.tabs(["📄 Pages Analyzed", "⚠️ Issues Detail", "🔗 Link Audit"])
            
            with tab1:
                df_pages = pd.DataFrame(pages_data).drop(columns=["_issues", "_warnings", "_links"])
                st.dataframe(df_pages, use_container_width=True)
                
            with tab2:
                has_issues = False
                for p in pages_data:
                    if p["_issues"] or p["_warnings"]:
                        has_issues = True
                        with st.expander(f"{p['URL']} ({p['Issues Count']} issues)"):
                            for i in p["_issues"]: st.markdown(f"🔴 {i}")
                            for w in p["_warnings"]: st.markdown(f"🟡 {w}")
                if not has_issues:
                    st.info("No issues found! Great job.")

            with tab3:
                df_links = pd.DataFrame(links_data)
                
                # Filter buttons
                link_filter = st.radio("Filter Links", ["All", "Broken Only (4xx/5xx)"], horizontal=True)
                if link_filter == "Broken Only (4xx/5xx)":
                    df_links = df_links[pd.to_numeric(df_links['Status'], errors='coerce') >= 400]
                
                st.dataframe(df_links, use_container_width=True)
