import asyncio
import os
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

async def generate_session():
    async with async_playwright() as p:
        # Launch browser in headed mode so user can interact
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("Opening Flipkart Minutes...")
        await page.goto("https://www.flipkart.com/grocery-supermart-store")

        print("\nACTION REQUIRED:")
        print("1. Click on the 'Delivery to' or 'Location' selector.")
        print("2. Enter your PIN code and select your specific address/location.")
        print("3. Ensure the 'Minutes' or 'Grocery' section is correctly showing items for your area.")
        print("4. Once done, come back here and press Enter.")
        
        input("\nPress Enter after you have set your location in the browser...")

        # Save storage state (cookies, local storage) to a file
        await context.storage_state(path="session.json")
        print("\nSession saved to session.json!")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(generate_session())
