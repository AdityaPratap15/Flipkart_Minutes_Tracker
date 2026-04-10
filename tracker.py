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

# Product URLs to track
PRODUCT_URLS = [
    "https://www.flipkart.com/kwality-wall-s-belgian-chocolate-magnum-almond/p/itmb0b148f889a39",
    "https://www.flipkart.com/baskin-robbins-mango-mania-ice-cream/p/itm88eba3bec41da",
    "https://www.flipkart.com/amul-butterscotch-gold-icecream/p/itmfc872efabbfb2",
    "https://www.flipkart.com/get-a-way-mango-ice-cream/p/itm4fac05f8f6d30",
    "https://www.flipkart.com/chillfi-hocco-kulfi-shahi-malai-matka/p/itm9044507551246",
    "https://www.flipkart.com/havmor-ice-cream-strawberry-mahabaleshwar/p/itmff5a5224d7281",
    "https://www.flipkart.com/vadilal-red-velvet-badabite-ice-cream/p/itmd68f8250ae248",
    "https://www.flipkart.com/kwality-wall-s-belgian-chocolate-magnum-brownie/p/itm4b46924bbbe42",
    "https://www.flipkart.com/baskin-robbins-pistachio-caramel-ice-cream-cone/p/itm5df2cff651a68",
    "https://www.flipkart.com/vadilal-vanilla-strawberry-pista-cassata-ice-cream-cake/p/itm5f91099d2ba7f",
    "https://www.flipkart.com/chiilfi-hocco-kulfi-kesar-pista/p/itm2e6fa4f4fdbe0",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-cookies-n-cone/p/itm7b7fae6785ada",
    "https://www.flipkart.com/magnum-belgian-chocolate-caramel-pop/p/itm016a8a4b7de59",
    "https://www.flipkart.com/amul-butterscotch-gold-tricone-icecream/p/itm8385478a6bf33",
    "https://www.flipkart.com/kwality-wall-s-butterscotch-cornetto/p/itm217d47c8889ad",
    "https://www.flipkart.com/kwality-wall-s-chocolate-double-cornetto/p/itm5bf89a7a181b0",
    "https://www.flipkart.com/amul-chocolate-tricone-cookie-crunch-delight-disc/p/itmfd02ef48b2ba0",
    "https://www.flipkart.com/amul-kulfi-rajbhog/p/itmadddeca914529",
    "https://www.flipkart.com/amul-chocolate-chocochips/p/itm75cdacd6f4060",
    "https://www.flipkart.com/amul-kulfi-punjabi/p/itm057cb11ee00ce",
    "https://www.flipkart.com/kwality-walls-almond-cornetto-crunch/p/itmaf110398ab11b",
    "https://www.flipkart.com/kwality-wall-s-vanilla-dairy-factory-icecream/p/itm12d935a290d37",
    "https://www.flipkart.com/vadilal-butterscotch-crunchy-gourmet-ice-cream-tub/p/itmf0f20694a2784",
    "https://www.flipkart.com/baskin-robbins-almond-n-caramel-ice-cream-stick/p/itm007d2ebc07e2b",
    "https://www.flipkart.com/go-zero-sitaphal-simply-guilt-free-icecream-cup/p/itm4946d6822a6d9",
    "https://www.flipkart.com/amul-chocolate-sundae-magic/p/itm158ec0f04f1f7",
    "https://www.flipkart.com/amul-choco-brownie-chocolate-sandwich-gold-icecream/p/itmbf416fddc9cdc",
    "https://www.flipkart.com/amul-vanilla-gold/p/itmdd4a9290a4431",
    "https://www.flipkart.com/amul-mango-frozen-yogurt/p/itmde1b92f69aefb",
    "https://www.flipkart.com/kwality-wall-s-chocolate-dairy-factory-chocochips-icecream/p/itm9f4f7d3ecde31",
    "https://www.flipkart.com/vadilal-american-nuts-flingo-ice-cream-cone/p/itm151aaeb03b307",
    "https://www.flipkart.com/kwality-wall-s-butterscotch-dairy-factory-icecream-tub/p/itm7b075f572afd7",
    "https://www.flipkart.com/vadilal-pista-almond-fudge-groumet-ice-cream-cup/p/itm3049d6d185ae7",
    "https://www.flipkart.com/kwality-wall-s-mango-aamras-stick/p/itm32379dcc5b896",
    "https://www.flipkart.com/baskin-robbins-hazelnut-ice-cream-cone/p/itmade7ce014081f",
    "https://www.flipkart.com/vadilal-dark-chocolate-ice-cream-cone/p/itmc68f8bccd49fc",
    "https://www.flipkart.com/kwality-walls-chocolate-cadbury-crackle-tub/p/itm25431d5b6a05b",
    "https://www.flipkart.com/vadilal-kesar-pista-badam/p/itme775db2cdccd9",
    "https://www.flipkart.com/hocco-choco-brownie-ice-cream/p/itm58f3c8ef5164b",
    "https://www.flipkart.com/kwality-wall-s-belgian-chocolate-magnum-truffle/p/itm446723167bc7e",
    "https://www.flipkart.com/chiilfi-hocco-kulfi-shahi-malai/p/itm3fd68493f6dad",
    "https://www.flipkart.com/baskin-robbins-kulfi-pista/p/itmf4051681e26b2",
    "https://www.flipkart.com/go-zero-malai-kulfi-guilt-free-ice-cream-stick/p/itm28a30dd3b8036",
    "https://www.flipkart.com/amul-chocolate-ice-cream-brownie/p/itm2ebe74c6a8ce6",
    "https://www.flipkart.com/chillfi-hocco-kulfi-shahi-malai/p/itmd408605a161c8",
    "https://www.flipkart.com/hocco-chocolate-tiramasu/p/itm709f25c62517c",
    "https://www.flipkart.com/kwality-wall-s-chocolate-feast-cadbury-crackle/p/itm97d7f231c1357",
    "https://www.flipkart.com/baskin-robbins-chocolate-dutch-ice-cream-cup/p/itm1e166c096260f",
    "https://www.flipkart.com/hocco-chocolate-chillo-dark-cone-ice-cream/p/itm07d8338af7aae",
    "https://www.flipkart.com/go-zero-vanilla-chocolate-vanilla-choco-fudge-guilt-free-ice-cream-cone/p/itm6a860bc214b61",
    "https://www.flipkart.com/vadilal-vanilla-sandwich-ice-cream/p/itm0baadf5401d76",
    "https://www.flipkart.com/go-zero-vanilla-french-guilt-free-ice-cream-tub/p/itm7ab5bf09fb034",
    "https://www.flipkart.com/baskin-robbins-red-velvet-slice-cake/p/itmc4f8f8714ea0f",
    "https://www.flipkart.com/hocco-mango-ratnagiri/p/itmca36c3ddf2c7e",
    "https://www.flipkart.com/amul-chocolate-cassata-icecream/p/itm98fe63603d690",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-dark/p/itmd35f2c38c8e7d",
    "https://www.flipkart.com/mother-dairy-cashew-badam-rabri-tub/p/itmf8b95fe0af2b7",
    "https://www.flipkart.com/kwality-walls-oreo-cream-cup/p/itm4d5b59b4e6e72",
    "https://www.flipkart.com/amul-almond-skyscop-dreamy-icecream/p/itm64d1182f0a2bf",
    "https://www.flipkart.com/kwality-wall-s-oreo-tub/p/itmfe2bc9a077e3f",
    "https://www.flipkart.com/amul-cookies-cream-gold-ice/p/itmdd8d94c78b937",
    "https://www.flipkart.com/vadilal-strawberry-swirl-cake-flingo-ice-cream-cone/p/itmdcac31ff9db95",
    "https://www.flipkart.com/hocco-cookies-cream/p/itm7c7d69591f413",
    "https://www.flipkart.com/mother-dairy-shahi-malai-mewa/p/itm4757afafedccb",
    "https://www.flipkart.com/amul-choco-chips-gold-icecream/p/itm2c5514fb73bc8",
    "https://www.flipkart.com/amul-chocolate-frostik-icecream/p/itm420b03da28f11",
    "https://www.flipkart.com/kwality-wall-s-pista-magnum-ice-cream-stick/p/itm79e1943e9b53b",
    "https://www.flipkart.com/kwality-wall-s-strawberry-vanilla-cornetto/p/itmf698f314aefb8",
    "https://www.flipkart.com/vadilal-american-nuts-gourmet-ice-cream-tub/p/itm256fe0d213ae2",
    "https://www.flipkart.com/vadilal-butterscotch-nutty-flingo-ice-cream-cone/p/itm8ab6d668d35fb",
    "https://www.flipkart.com/hocco-belgian-choconut-ice-cream-tub/p/itm92fabf234c256",
    "https://www.flipkart.com/kwality-wall-s-choco-brownie-fudge-ice-cream-tub/p/itma1ef582184d0e",
    "https://www.flipkart.com/kwality-wall-s-vanilla-choco-sandwich/p/itm0f6463411f6bc",
    "https://www.flipkart.com/hocco-hazelnut-mudslide/p/itm3725a69a6b4a9",
    "https://www.flipkart.com/hocco-chocolate-death/p/itm54b8029309f8a",
    "https://www.flipkart.com/hocco-blueberry-cheesecake-ice-cream-tub/p/itm8b420a8910c27",
    "https://www.flipkart.com/go-zero-chocolate-belgian-dark-guilt-free-ice-cream-cup/p/itmf08dbc4cab5a3",
    "https://www.flipkart.com/hocco-chocolate-oh-nutty-cone-ice-cream/p/itm3adf93ea0508b",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-chocolate-cakes/p/itmd17a9e4b42574",
    "https://www.flipkart.com/vadilal-mango-badabite-ice-cream/p/itm4f95b0a8938ad",
    "https://www.flipkart.com/amul-chocolate-crunch/p/itm76e1e0a0ec5f2",
    "https://www.flipkart.com/chillfi-hocco-kulfi-golden-pistachio-matka/p/itmddd61f8bde305",
    "https://www.flipkart.com/vadilal-kulfi-matka/p/itmda86d7ea73652",
    "https://www.flipkart.com/amul-strawberry-sundae-magic/p/itmc22e6b5fe91a7",
    "https://www.flipkart.com/amul-mango-ice-cream-king-alphoso-gold/p/itm24ee8f080d340",
    "https://www.flipkart.com/havmor-ice-cream-kulfi-matka-kulfi-novelty/p/itm15f2570ac5b01",
    "https://www.flipkart.com/havmor-ice-cream-butterscotch-butter-scotch-cookie-cake/p/itmc83c52751cfbf",
    "https://www.flipkart.com/amul-fruit-nut-fantasy/p/itm150129cbe28f7",
    "https://www.flipkart.com/amul-butterscotch-tricone/p/itm3cd14eb5fa959",
    "https://www.flipkart.com/hocco-choco-chips-bix-ice-cream/p/itm3d84eed23ea28",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-zulubar-candy/p/itmbda818ecf9ff7",
    "https://www.flipkart.com/vadilal-almond-choco-crunch-badabite-ice-cream/p/itm7e69a6f98de99",
    "https://www.flipkart.com/havmor-ice-cream-sitaphal-sitafal/p/itmc3c5f91bb4bd2",
    "https://www.flipkart.com/go-zero-vanilla-french-guilt-free-ice-cream-cup/p/itmfe6b6b50efbd8",
    "https://www.flipkart.com/mother-dairy-kesar-pista-tub/p/itme181b4cf55622",
    "https://www.flipkart.com/amul-fruit-nut-fantasy/p/itm91c42d4c6deae",
    "https://www.flipkart.com/vadilal-vanilla-premium-gourmet-ice-cream-tub/p/itm68b9acb3a0b37",
    "https://www.flipkart.com/vadilal-chocolate-silk-gourmet-ice-cream-cup/p/itm8a7a42c1d84cf",
    "https://www.flipkart.com/kwality-wall-s-black-forest-feast/p/itma8510024c5db9",
    "https://www.flipkart.com/kwality-walls-choco-brownie-cornetto/p/itm30b19a12b3373",
    "https://www.flipkart.com/amul-kulfi-badshahi/p/itm7039d378a3b43",
    "https://www.flipkart.com/kwality-wall-s-mango-dairy-factory-alphonso-icecream/p/itm85450800d82e1",
    "https://www.flipkart.com/go-zero-strawberry-sundae-guilt-free-ice-cream-cup/p/itm5edcaebf48c2a",
    "https://www.flipkart.com/baskin-robbins-mississippi-mud-ice-cream/p/itm6e4248cf2455c",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-zulubar-dark-crunch-candy/p/itme7ffa7d3043b6",
    "https://www.flipkart.com/havmor-ice-cream-chocolate-cookiesandwich-chocolate-novelty/p/itme158ecbede0b7",
    "https://www.flipkart.com/beardo-godfather-perfume-premium-strong-long-lasting-fragrance-aromatic-gift-eau-de-parfum-50-ml/p/itmbec4b62239965",
    "https://www.flipkart.com/bellavita-white-oud-long-lasting-unisex-eau-de-parfum-100-ml/p/itm64e4843953a3c",
    "https://www.flipkart.com/fogg-xtremo-perfume-scent-ii-long-lasting-eau-de-parfum-100-ml/p/itm0d0e7e3ac835d",
    "https://www.flipkart.com/bellavita-ceo-man-eau-de-parfum-long-lasting-notes-tonka-agarwood-ambergris-perfume-100-ml/p/itm3ccce2a855d68",
    "https://www.flipkart.com/denver-imperial-perfume-premium-long-lasting-eau-de-parfum-70-ml/p/itm3c944942ec463",
    "https://www.flipkart.com/denver-sporting-club-edp-srk-s-favorite-luxury-gift-pack-20ml-x-4-perfume-eau-de-parfum-80-ml/p/itm4b3b1cb81955c",
    "https://www.flipkart.com/renee-bloom-eau-de-parfum-50-ml/p/itm9b8c79edc5d4e",
    "https://www.flipkart.com/man-company-night-long-lasting-perfume-50-ml/p/itm71a24f233de53",
    "https://www.flipkart.com/engage-floral-fresh-perfume-17-ml/p/itmf3v9rcz53gwc8",
    "https://www.flipkart.com/engage-classic-woody-pocket-perfume-17-ml/p/itmf3wgygvf9hg3w",
    "https://www.flipkart.com/wild-stone-night-rider-eau-de-parfum-men-30-ml/p/itm83d6e8ff5fe88",
    "https://www.flipkart.com/beardo-best-day-perfume-gift-set-strong-long-lasting-fresh-aquatic-gift-4-x-20-ml-eau-de-parfum-80/p/itmd824fd6852aa2",
    "https://www.flipkart.com/engage-gift-set-celebrations-perfume-spray-long-lasting-wedding-hamper-itc-25mlx4-100-ml/p/itm308a4b6d7c244",
    "https://www.flipkart.com/fien-mcaffeine-rush-perfume-gift-set-12-hrs-long-lasting-vanilla-muse-cherry-wine-eau-de-parfum-80-ml/p/itm03656ab2e89a4",
    "https://www.flipkart.com/wild-stone-luxury-perfume-gift-set-men-20ml-x-4-long-lasting-premium-fragrance-eau-de-parfum-80-ml/p/itm72c1778d6b53b",
    "https://www.flipkart.com/park-avenue-luxury-perfume-gift-set-eau-de-parfum-80-ml/p/itme48d5154a14d7",
    "https://www.flipkart.com/wild-stone-code-luxury-perfume-gift-set-men-20ml-x-3-long-lasting-premium-him-eau-de-parfum-60-ml/p/itm4c454ee926de7",
    "https://www.flipkart.com/carlton-london-women-luxury-perfume-gift-set-4x20ml-eau-de-parfum-80-ml/p/itmf47da59228446",
    "https://www.flipkart.com/wild-stone-hydra-energy-perfume-100-ml/p/itmf3wjfuypqwvdh",
    "https://www.flipkart.com/fogg-fresh-woody-premium-perfume-scent-long-lasting-eau-de-parfum-120-ml/p/itm70ef8653b80ef",
    "https://www.flipkart.com/engage-yang-skin-friendly-women-perfume-fruity-fragrance-scent-ideal-gifting-eau-de-parfum-100-ml/p/itmc61844d978387",
    "https://www.flipkart.com/denver-imperial-perfume-premium-long-lasting-eau-de-parfum-100-ml/p/itm3c944942ec463",
    "https://www.flipkart.com/fogg-scent-intensio-eau-de-parfum-100-ml/p/itmf3wgyy3zhfzgk",
    "https://www.flipkart.com/carlton-london-luxury-perfume-iconic-gift-set-men-eau-de-parfum-4x20-ml-80/p/itmf9f8cec1e66d5",
    "https://www.flipkart.com/bellavita-luxury-unisex-perfume-gift-set-4x20-ml-eau-de-parfum-80/p/itm049100feb010d",
    "https://www.flipkart.com/engage-yang-women-perfume-fruity-fragrance-scent-ideal-gift-women-skin-friendly-eau-de-parfum-50-ml/p/itm4c3b4b2a32a05",
    "https://www.flipkart.com/beardo-godfather-whisky-smoke-perfume-strong-long-lasting-fragrance-aromatic-gift-eau-de-parfum-100-ml/p/itm1a6193f8aa0b6",
    "https://www.flipkart.com/beardo-godfather-perfume-premium-strong-long-lasting-fragrance-aromatic-gift-eau-de-parfum-20-ml/p/itmbec4b62239965",
    "https://www.flipkart.com/engage-femme-skin-friendly-women-perfume-citrus-fragrance-scent-ideal-gifting-eau-de-parfum-100-ml/p/itm5d68ac9daf3dc",
    "https://www.flipkart.com/french-essence-luxury-triumph-long-lasting-fragrance-eau-de-parfum-30-ml/p/itmef99239ebe474",
    "https://www.flipkart.com/wild-stone-hydra-energy-eau-de-parfum-100-ml/p/itm7c728badcb9d7",
    "https://www.flipkart.com/engage-homme-men-perfume-citrus-fresh-fragrance-scent-gift-men-long-lasting-eau-de-parfum-50-ml/p/itm903affbc20d64",
    "https://www.flipkart.com/beardo-whisky-smoke-edp-perfume-strong-long-lasting-spicy-woody-oudh-scent-gift-eau-de-parfum-50-ml/p/itm06f5948ae9c3e",
    "https://www.flipkart.com/engage-gift-set-moments-perfume-long-lasting-fragrance-itc-wedding-hamper-pack-1-eau-de-parfum-100-ml/p/itmae81fbbb2b9ad",
    "https://www.flipkart.com/skinn-titan-tales-rio-perfume-eau-de-parfum-100-ml/p/itm5563ca21310ac",
    "https://www.flipkart.com/secret-temptation-romance-eau-de-parfum-50-ml/p/itmf3wgvfr4qqcfz",
    "https://www.flipkart.com/bellavita-ros-eau-de-parfum-long-lasting-floral-fragrance-women-20-ml/p/itm3b8b272bec168",
    "https://www.flipkart.com/bellavita-date-perfum-notes-pink-pepper-red-fruits-eau-de-parfum-100-ml/p/itmc8bd030f22f2d",
    "https://www.flipkart.com/just-herbs-pure-fragrances-refreshing-energising-trio-perfume-set-3-50ml-eau-de-parfum-150-ml/p/itm1ea607c0606a7",
    "https://www.flipkart.com/bellavita-gift-set-4x20-ml-luxury-scent-long-lasting-fragrance-perfume-80/p/itmf9f1fc180df04",
    "https://www.flipkart.com/fogg-gift-set-premium-perfume-scent-long-lasting-eau-de-parfum-90-ml/p/itme758f78666ef5",
    "https://www.flipkart.com/wild-stone-hydra-energy-perfume-eau-de-parfum-50-ml/p/itmfg3hsupzjvgya",
    "https://www.flipkart.com/envy-red-luxury-perfume-gift-set-20-ml-x-4-eau-de-parfum-80/p/itm8fa6cf2338658",
    "https://www.flipkart.com/plum-bodylovin-luxe-perfume-gift-set-3-x-15ml-long-lasting-perfumes-fresh-floral-eau-de-parfum-45-ml/p/itm503ffe708db34",
    "https://www.flipkart.com/engage-gift-set-moments-unisex-perfume-long-lasting-fragrance-pack-2-wedding-hamper-eau-de-parfum-200-ml/p/itmeb80e86647c34",
    "https://www.flipkart.com/just-herbs-intense-oud-vanilla-perfect-gifting-luxury-scent-long-lasting-fragrance-eau-de-parfum-50-ml/p/itmb4e665050e4c0",
    "https://www.flipkart.com/fogg-impressio-perfume-scent-long-lasting-eau-de-parfum-100-ml/p/itm2a9f18275901a",
    "https://www.flipkart.com/denver-hamilton-edp-srk-s-favorite-luxury-gift-pack-20ml-x-4-perfume-eau-de-parfum-80-ml/p/itm3363524a5d613",
    "https://www.flipkart.com/wild-stone-ultra-sensual-perfume-deodorant-spray-long-lasting-body-men/p/itm8982b1c5f17f1",
    "https://www.flipkart.com/beardo-dark-side-godfather-edp-perfume-set-2-pcs-strong-long-lasting-fragrance-eau-de-parfum-40-ml/p/itm0b65b065185db",
    "https://www.flipkart.com/wild-stone-premium-perfume-gift-set-men-ultra-sensual-edge-night-rider-red-30-mlx4-eau-de-parfum-120-ml/p/itm7a5b1e517ff91",
    "https://www.flipkart.com/bellavita-mood-collection-perfume-gift-set-her-3x15ml-long-lasting-fragrances-eau-de-parfum-45-ml/p/itm871c20192b65a",
    "https://www.flipkart.com/wild-stone-edge-perfume-eau-de-parfum-50-ml/p/itm220c93994e085",
    "https://www.flipkart.com/wild-stone-edge-forest-spice-hydra-energy-ultra-sensual-perfume-combo-pack-4-30-ml-each-eau-de-parfum-120/p/itm8bc3a14404e66",
    "https://www.flipkart.com/wild-stone-hydra-energy-perfume-eau-de-parfum-50-ml/p/itm7f6811bf6fd2d",
    "https://www.flipkart.com/carlton-london-men-enigma-gift-set-3-50ml-each-eau-de-parfum-150-ml/p/itm754cade64a5fa",
    "https://www.flipkart.com/engage-gift-set-luxury-unisex-travel-perfume-long-lasting-itc-wedding-hamper-25mlx4-eau-de-parfum-100-ml/p/itm3eba0fe36f4be",
    "https://www.flipkart.com/man-company-black-perfume-eau-de-toilette-50-ml/p/itm362a7d107d090",
    "https://www.flipkart.com/wild-stone-edge-eau-de-parfum-100-ml/p/itm36803446985c8",
    "https://www.flipkart.com/wild-stone-ultra-sensual-perfume-100-ml/p/itmbddfd5a31fa32",
    "https://www.flipkart.com/bergamot-beaute-gentleman-perfume-tux-luxury-gift-set-men-long-lasting-2x15-ml-combo-30/p/itme7bd090888b51",
    "https://www.flipkart.com/oscar-big-shot-jazz-club-privee-2x100ml-eau-de-parfum-200-ml/p/itm2d1a23a8cf165",
    "https://www.flipkart.com/fogg-gift-set-pack-4-premium-perfume-scent-long-lasting-eau-de-parfum-120-ml/p/itm77b8bb1ec9f7e",
    "https://www.flipkart.com/renee-eau-de-parfum-combo-4-15ml-each-60-ml/p/itm0d38d5b6cda7e",
    "https://www.flipkart.com/oscar-forever-midnight-perfume-long-lasting-fragrance-edp-scent-pack-1-eau-de-parfum-100-ml/p/itmee55757867e9e",
    "https://www.flipkart.com/engage-gift-set-celebrations-perfume-spray-long-lasting-wedding-hamper-itc-25mlx4-100-ml/p/itmb08821bef326d",
    "https://www.flipkart.com/renee-eau-de-parfum-bloom-15ml-15-ml/p/itm8eddba3551f30",
    "https://www.flipkart.com/man-company-polo-black-perfume-premium-long-lasting-fragrance-eau-de-parfum-100-ml/p/itm7c27889bf890a",
    "https://www.flipkart.com/renee-bloom-8ml-eau-de-parfum-8-ml/p/itma996dc5d5f2b9",
    "https://www.flipkart.com/fogg-fresh-fougere-premium-perfume-scent-long-lasting-eau-de-parfum-120-ml/p/itm51a3efb4fe6aa",
    "https://www.flipkart.com/wild-stone-intense-no-gas-deo-travel-pack-black-ocean-trance-wood-40ml-each-mini-deodorant-spray-men/p/itmc28da653758e8",
    "https://www.flipkart.com/denver-gentlemen-collection-edp-20-ml-x-3-perfume-60/p/itm3568d7e06d148",
    "https://www.flipkart.com/beardo-godfather-perfume-premium-strong-long-lasting-fragrance-aromatic-gift-eau-de-parfum-100-ml/p/itm6939ca23d8cfb",
    "https://www.flipkart.com/fogg-fresh-oriental-premium-perfume-scent-long-lasting-eau-de-parfum-120-ml/p/itme1d1fe8268edc",
    "https://www.flipkart.com/wild-stone-ultra-sensual-eau-de-parfum-eau-50-ml/p/itm7b6673815caf4",
    "https://www.flipkart.com/envy-natural-spray-perfume-premium-and-long-lasting-eau-de-parfum-70-ml/p/itma27f94c6b7e4c",
    "https://www.flipkart.com/bellavita-luxury-perfume-gift-set-long-lasting-fragrance-eau-de-parfum-80-ml/p/itmf17ee6c66d0f7",
    "https://www.flipkart.com/skinn-titan-nude-single-pack-eau-de-parfum-20-ml/p/itmf3wgwxbrfhabe",
    "https://www.flipkart.com/bellavita-unisex-scents-gift-set-4x20ml-long-lasting-perfume-woody-musk-fragrance-notes-eau-de-parfum-80-ml/p/itm61a47fd582ca6",
    "https://www.flipkart.com/secret-temptation-fragrance-gift-set-ruby-daisy-jazz-30ml-each-long-lasting-eau-de-parfum-90-ml/p/itm9d9e496cc5e9a",
    "https://www.flipkart.com/guess-girl-belle-eau-de-toilette-100-ml/p/itmf3wgypd5tfkgz",
    "https://www.flipkart.com/french-essence-luxury-intimate-long-lasting-fragrance-eau-de-parfum-30-ml/p/itm74e4144673656",
    "https://www.flipkart.com/wild-stone-ultra-sensual-perfume-eau-de-parfum-men-100-ml/p/itmda793148aacde",
    "https://www.flipkart.com/set-wet-signature-eau-de-parfum-fire-100-ml/p/itm568fc0d572e51",
    "https://www.flipkart.com/secret-temptation-dream-eau-de-parfum-50-ml/p/itmfgpncmzdnkvj5",
    "https://www.flipkart.com/park-avenue-harmony-eau-de-parfum-100-ml/p/itmc3660619ac534",
    "https://www.flipkart.com/skinn-titan-celeste-single-pack-eau-de-parfum-20-ml/p/itmf3wgwguwnjxjh",
    "https://www.flipkart.com/bellavita-honey-oud-honey-floral-scent-edp-fragrance-eau-de-parfum-100-ml/p/itm30269018a5804",
    "https://www.flipkart.com/denver-imperial-perfume-100-ml-deo-180-280/p/itmfe4cff60be1dd",
    "https://www.flipkart.com/yardley-london-morning-dew-floral-scent-daily-wear-perfume-100-ml/p/itmb17edf809fc39",
    "https://www.flipkart.com/envy-natural-spray-perfume-edp-ii-premium-long-lasting-eau-de-parfum-70-ml/p/itmc4d4f7b61434d",
    "https://www.flipkart.com/engage-gift-set-moments-perfume-long-lasting-fragrance-wedding-hamper-itc-50mlx2-eau-de-parfum-100-ml/p/itm297c80a223996",
    "https://www.flipkart.com/french-essence-luxury-bloom-oud-gift-set-30-ml-x-2-long-lasting-fragrance-eau-de-parfum-60/p/itm3314eb843bcbd",
    "https://www.flipkart.com/french-essence-luxury-bloom-long-lasting-fragrance-eau-de-parfum-30-ml/p/itmaab6ea3f0f040",
    "https://www.flipkart.com/engage-fantasia-perfume-long-lasting-floral-spicy-for-special-occasions-tester-free-eau-de-parfum-100-ml/p/itm4344eb4350400",
    "https://www.flipkart.com/ajmal-oud-dubai-long-lasting-unisex-perfume-eau-de-parfum-100-ml/p/itm830a46a69c13d",
    "https://www.flipkart.com/titan-skinn-tales-oslo-eau-de-parfum-100-ml/p/itm20d9acdaa2971",
    "https://www.flipkart.com/engage-gift-set-luxury-travel-perfume-long-lasting-fragrance-wedding-hamper-25mlx4-eau-de-parfum-100-ml/p/itm25d153862086e",
    "https://www.flipkart.com/engage-gift-set-luxury-travel-perfume-long-lasting-fragrance-wedding-hamper-25mlx4-eau-de-parfum-100-ml/p/itm11a9367d6f9ae",
    "https://www.flipkart.com/yardley-london-gentleman-classic-fresh-woody-fougere-daily-wear-perfume-100-ml/p/itm8d47fb1757736",
    "https://www.flipkart.com/engage-w1-perfume-body-spray-women/p/itmf3v9rphzhaahz",
    "https://www.flipkart.com/man-company-blanc-night-fire-oud-20ml-gift-set-a-gentleman-s-mood-eau-de-parfum-80-ml/p/itm62c96aab5e05f",
    "https://www.flipkart.com/beardo-blue-blood-long-lasting-perfume-intense-aromatic-aquatic-premium-scent-eau-de-parfum-30-ml/p/itm787d663d092e1",
    "https://www.flipkart.com/beardo-whisky-smoke-bourbon-perfume-edp-oriental-woody-leathery-strong-long-lasting-eau-de-parfum-50-ml/p/itm9370df8567bd4",
    "https://www.flipkart.com/park-avenue-euphoria-eau-de-parfum-100-ml/p/itm8e246a197d073",
    "https://www.flipkart.com/envy-blue-luxury-perfume-gift-set-20-ml-x-4-eau-de-parfum-80/p/itm134e782490d1a",
    "https://www.flipkart.com/beardo-mariner-edp-perfume-fresh-aqua-notes-strong-hints-long-lasting-aroma-eau-de-parfum-50-ml/p/itm01e3e1b2dcfe9",
    "https://www.flipkart.com/man-company-blanc-edt-luxury-perfume-men-eau-de-toilette-50-ml/p/itm2bdb5c41e05f0",
    "https://www.flipkart.com/secret-temptation-luxury-perfume-gift-set-women-romance-adore-dream-bliss-20-ml-x-4-eau-de-parfum-80/p/itmc47c92f5a52a0",
    "https://www.flipkart.com/beardo-perfume-trial-kit-gift-set-men-strong-long-lasting-fragrances-10-x-5-ml-eau-de-parfum-50/p/itmcc3b12bc93291",
    "https://www.flipkart.com/freed-musk-bomb-perfume-women-intense-strong-long-lasting-spicy-oriental-eau-de-parfum-20-ml/p/itm0499ea2d852e7",
    "https://www.flipkart.com/park-avenue-amazon-woods-eau-de-parfum-120-ml/p/itmff1678f6d17e8",
    "https://www.flipkart.com/park-avenue-gift-set-men-conquer-harmony-eau-de-parfum-200-ml/p/itm0e37195f32186",
    "https://www.flipkart.com/fogg-xtremo-perfume-scent-long-lasting-eau-de-parfum-75-ml/p/itmfbba15259df2e",
    "https://www.flipkart.com/fogg-impressio-perfume-scent-long-lasting-eau-de-parfum-50-ml/p/itmb2a18c2bae7b2",
    "https://www.flipkart.com/set-wet-fire-ice-perfume-men-woody-citrusy-long-lasting-perfume-pack-2-eau-de-parfum-200-ml/p/itm3cc4cccdfcca6",
    "https://www.flipkart.com/adrenex-dzire-perfume-deodorant-spray-men/p/itmec73a0ea9a100",
    "https://www.flipkart.com/beardo-whisky-smoke-body-spray-bourbon-edp-gift-strong-long-lasting-fragrance-eau-de-parfum-170-ml/p/itm1d64aa299b77f",
    "https://www.flipkart.com/fastrack-bold-trance-guys-eau-de-parfum-100-ml/p/itm985d5c0451aa6",
    "https://www.flipkart.com/beardo-g-t-perfume-edp-intense-co-crafted-nisaki-strong-long-lasting-eau-de-parfum-30-ml/p/itm659667811fc5c",
    "https://www.flipkart.com/bellavita-date-women-perfume-notes-pink-pepper-red-fruits-long-lasting-fragrance-eau-de-parfum-20-ml/p/itm347eac4d81f20",
    "https://www.flipkart.com/french-essence-luxury-aura-long-lasting-fragrance-eau-de-parfum-60-ml/p/itm9463492634da8"

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
                # Navigate to the sanitized URL with a faster wait strategy
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                
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