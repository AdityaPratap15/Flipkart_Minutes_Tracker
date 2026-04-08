import asyncio
import os
import re
import json
import random
import requests
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Supabase configuration
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Telegram configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_alert(product_name: str, old_price: int, new_price: int):
    """Send a notification message via Telegram bot."""
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram configuration missing. Skipping alert.")
        return

    message = (
        f"🚨 *Price Drop Detected!*\n\n"
        f"*Product Name:* {product_name}\n"
        f"*Old Price:* ₹{old_price}\n"
        f"*New Price:* ₹{new_price}\n"
        f"📉 *Savings:* ₹{old_price - new_price}"
    )
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print(f"Telegram alert sent for {product_name}")
    except Exception as e:
        print(f"Failed to send Telegram alert: {e}")

def get_last_price(product_name: str):
    """Fetch the latest price for a product from Supabase."""
    try:
        response = supabase.table("price_logs") \
            .select("price") \
            .eq("product_name", product_name) \
            .order("checked_at", desc=True) \
            .limit(1) \
            .execute()
        
        if response.data and len(response.data) > 0:
            return response.data[0]["price"]
        return None
    except Exception as e:
        print(f"Error fetching last price: {e}")
        return None

def log_price(product_name: str, price: int):
    """Insert a new price log into Supabase."""
    try:
        data = {
            "product_name": product_name,
            "price": price
        }
        supabase.table("price_logs").insert(data).execute()
        print(f"Successfully logged {product_name} at ₹{price}")
    except Exception as e:
        print(f"Error logging price: {e}")

def parse_price(price_str: str) -> int:
    """Extract integer price from messy strings (e.g., '₹45,999' or 'Price: 450.00')."""
    if not price_str:
        return 0
    
    try:
        # Step 1: Specifically look for numbers following a currency symbol (₹)
        # Handle cases like "₹24,999", "₹ 24999", "MRP: ₹24,999"
        match = re.search(r'₹\s*([\d,.]+)', price_str)
        if match:
            clean_str = match.group(1).replace(",", "")
            # Handle decimals if present (e.g. 450.00 -> 450)
            return int(float(clean_str))
        
        # Step 2: Fallback - Extract all digits if no ₹ symbol is found
        # Good for strings like "24999", "Price: 24,999"
        clean_str = "".join(filter(lambda x: x.isdigit() or x == '.', price_str))
        if clean_str:
            return int(float(clean_str))
        
        return 0
    except (ValueError, TypeError, Exception):
        return 0

def trigger_price_drop_alert(product_name: str, old_price: int, new_price: int):
    """Trigger an alert for a price drop."""
    drop = old_price - new_price
    percent = (drop / old_price) * 100
    print(f"🚨 ALERT: PRICE DROP DETECTED! 🚨")
    print(f"Product: {product_name}")
    print(f"Old Price: ₹{old_price} -> New Price: ₹{new_price}")
    print(f"Save: ₹{drop} ({percent:.1f}% OFF)")
    
    # Trigger Telegram notification
    send_telegram_alert(product_name, old_price, new_price)

def extract_main_price(html_content: str) -> dict:
    """
    Extract the primary product price using BeautifulSoup.
    Isolates the main product container to avoid 'Related' or 'Sponsored' prices.
    Returns: {'name': str, 'price': int}
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Identify the primary product container
    # Flipkart's main product section usually contains the 'H1' or a specific ID
    # We look for the container that holds the product title (H1)
    main_container = None
    title_element = soup.find('h1') or soup.find(class_=re.compile(r'B_NuCI|VU-Z7S|U-u13y'))
    
    if title_element:
        # Move up to a common parent that likely holds the price (div containing both title and price)
        # Added '_3_vvyy' which is common for Hyperlocal product details
        main_container = title_element.find_parent('div', class_=re.compile(r'C_34S|aMa0q|DOYkY|_3_vvyy')) or \
                         title_element.parent.parent # Fallback to 2 levels up
    
    product_name = title_element.get_text().strip() if title_element else "Unknown Product"
    
    # Use the isolated container if found, otherwise search the whole soup
    search_scope = main_container if main_container else soup
    
    # 2. Extract Selling Price (Prioritize .Nx9bqj or ._30jeq3)
    # Specifically exclude MRP categories if possible by checking siblings/parents
    price_tags = search_scope.find_all(attrs={"class": re.compile(r'Nx9bqj|_30jeq3|_16Jk6d')})
    
    valid_prices = []
    for tag in price_tags:
        # Avoid strikethrough classes (MRP)
        parent_classes = str(tag.parent.get('class', []))
        if any(mrp_cls in parent_classes for mrp_cls in ['y9H60W', '_3I9_wc', 'v3H60W']):
            continue
            
        p = parse_price(tag.get_text())
        if 0 < p < 1000000:
            valid_prices.append(p)

    # 3. Fallback: Find any ₹ physically closest to the title or just any ₹
    if not valid_prices:
        # Search for any string with ₹ and digits
        all_text = soup.get_text()
        matches = re.findall(r'₹\s*([\d,]+)', all_text)
        for m in matches:
            p = parse_price(f"₹{m}")
            if 0 < p < 1000000:
                valid_prices.append(p)

    # Return the first found price (which usually is the main one on a sanitized URL)
    # or the minimum if we have several
    main_price = valid_prices[0] if valid_prices else 0
    return {"name": product_name, "price": main_price}

def compare_and_log_price(product_name: str, current_price: int):
    """Compare current price with database and log or alert accordingly."""
    print(f"--- Processing: {product_name} ---")
    print(f"Fetching last price from database...")
    last_price = get_last_price(product_name)
    
    if last_price is None:
        print(f"No previous data found for {product_name}. Initializing baseline.")
        log_price(product_name, current_price)
    elif current_price < last_price:
        trigger_price_drop_alert(product_name, last_price, current_price)
        log_price(product_name, current_price)
    elif current_price > last_price:
        diff = current_price - last_price
        print(f"📈 Price increased by ₹{diff} (₹{last_price} -> ₹{current_price}). Logging change.")
        log_price(product_name, current_price)
    else:
        print(f"✅ No change in price for {product_name} (Still ₹{current_price}). Skipping database log.")

# Product URLs to track - Add your Flipkart Minutes product links here
PRODUCT_URLS = [
    "https://www.flipkart.com/yardley-london-gentleman-classic-fresh-woody-fougere-daily-wear-perfume-100-ml/p/itm8d47fb1757736?pid=PERG3MY7HG3KSEGT&lid=LSTPERG3MY7HG3KSEGTJJ8LCC&marketplace=HYPERLOCAL&pageUID=1775641873199"
]

def extract_products_from_json(data):
    """Recursively search for product names and prices in Flipkart's JSON response."""
    products = []
    
    # Common keys Flipkart uses in their internal API for product data
    # Note: These names change, so we look for patterns
    if isinstance(data, dict):
        # Look for the 'value' or 'data' container that usually holds product info
        if "title" in data and "pricing" in data:
            name = data.get("title", "")
            price_info = data.get("pricing", {})
            # Prices are often inside 'finalPrice' or 'displayPrice'
            price = price_info.get("finalPrice", {}).get("value") or \
                    price_info.get("displayPrice", {}).get("value")
            if name and price:
                products.append({"name": name, "price": int(price)})
        
        # Recursive search in all dictionary values
        for v in data.values():
            products.extend(extract_products_from_json(v))
    
    elif isinstance(data, list):
        # Recursive search in all list items
        for item in data:
            products.extend(extract_products_from_json(item))
            
    return products

def sanitize_url(url: str) -> str:
    """Clean common hyperlocal session/UI parameters but keep the HYPERLOCAL marketplace."""
    # Remove common session/tracking parameters that might expire
    url = re.sub(r'&fm=[^&]*', '', url)
    url = re.sub(r'&pageUID=[^&]*', '', url)
    url = re.sub(r'&hl_lid=[^&]*', '', url)
    
    # Ensure marketplace=HYPERLOCAL is present if it was there originally
    # We do NOT replace it with FLIPKART as per user request
    return url

async def scrape_prices():
    """Main scraping loop with URL sanitization and robust price extraction."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="en-IN",
            storage_state="session.json" if os.path.exists("session.json") else None
        )
        page = await context.new_page()

        for original_url in PRODUCT_URLS:
            url = sanitize_url(original_url)
            print(f"\n🔍 Scraping URL: {url}")
            
            try:
                # Navigate to the sanitized URL
                await page.goto(url, wait_until="networkidle", timeout=60000)
                
                # Capture the HTML for BeautifulSoup processing
                html_content = await page.content()
                product_data = extract_main_price(html_content)
                
                product_name = product_data["name"]
                price = product_data["price"]

                if price > 0:
                    print(f"✅ Success (Isolated Main Product Container)!")
                    print(f"✨ {product_name[:40]}... | Price: ₹{price}")
                    compare_and_log_price(product_name, price)
                else:
                    # Original text-based detection if BeautifulSoup logic fails completely
                    print("⚠️ BS4 main container failed. Falling back to global search...")
                    price_selectors = [".Nx9bqj", "._30jeq3", "._16Jk6d"]
                    found_price = False
                    for selector in price_selectors:
                        try:
                            loc = page.locator(selector).filter(has_text="₹").first
                            if await loc.is_visible():
                                price = parse_price(await loc.inner_text())
                                if price > 0:
                                    # Fallback Title
                                    title_loc = page.locator("h1, .B_NuCI, .U-u13y").first
                                    p_name = (await title_loc.inner_text()).strip() if await title_loc.count() > 0 else "Product"
                                    compare_and_log_price(p_name, price)
                                    found_price = True
                                    break
                        except: continue

                    if not found_price:
                        print(f"❌ Could not extract price for {url}")

            except Exception as e:
                print(f"❌ Error during scraping: {str(e)}")
            
        await browser.close()

# Update parse_price slightly to accept kwarg for clarity if needed, or keep existing

if __name__ == "__main__":
    asyncio.run(scrape_prices())
