import re

def parse_price(price_str: str) -> int:
    """Extract integer price from messy strings."""
    if not price_str:
        return 0
    
    try:
        # Step 1: Specifically look for numbers following a currency symbol (₹)
        match = re.search(r'₹\s*([\d,.]+)', price_str)
        if match:
            clean_str = match.group(1).replace(",", "")
            return int(float(clean_str))
        
        # Step 2: Fallback - Extract digits if no ₹ symbol is found
        clean_str = "".join(filter(lambda x: x.isdigit() or x == '.', price_str))
        if clean_str:
            return int(float(clean_str))
        
        return 0
    except (ValueError, TypeError, Exception):
        return 0

# Test cases
test_cases = [
    ("₹24,999", 24999),
    ("₹ 45,678", 45678),
    ("Price: ₹300.50", 300),
    ("No symbol 1500", 1500),
    ("Messy string 2,345! Text", 2345),
    ("", 0),
    (None, 0),
    ("Only text", 0)
]

print("--- Testing Price Parser ---")
for text, expected in test_cases:
    result = parse_price(text)
    status = "✅ PASS" if result == expected else f"❌ FAIL (Got {result})"
    print(f"Input: '{text}' | Expected: {expected} | Result: {result} | {status}")
