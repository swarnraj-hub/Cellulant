import asyncio
import os
import random
import sys
from datetime import date, timedelta

import boto3
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

LOGIN_URL    = "https://app.tingg.africa/cas/login"
PAYMENTS_URL = "https://app.tingg.africa/payments/payment"
COUNTRIES    = ["Uganda", "Tanzania", "Kenya"]
S3_BUCKET    = os.environ.get("TINGG_S3_BUCKET", "payout-recon")
S3_PREFIX    = os.environ.get("TINGG_S3_PREFIX", "tingg/payments/raw/")


def _require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit(f"[ERROR] Environment variable {name!r} is not set.")
    return value


async def human_type(locator, text, wpm=60):
    delay_ms = int(60_000 / (wpm * 5))
    for char in text:
        await locator.type(char, delay=delay_ms + random.randint(-10, 30))


# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------

async def login(page):
    username = _require_env("TINGG_USERNAME")
    password = _require_env("TINGG_PASSWORD")

    print("[*] Loading login page ...")
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(3.0)

    if "login" not in page.url:
        print("[+] Already logged in.")
        return

    try:
        await page.wait_for_selector('input[type="text"], input[type="email"]', timeout=30_000)
    except PlaywrightTimeoutError:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] Username input not found. Screenshot -> tingg_error.png")

    print("[*] Entering credentials ...")
    user_input = page.locator('input[type="text"], input[type="email"]').first
    await user_input.click()
    await user_input.fill("")
    await human_type(user_input, username)
    await asyncio.sleep(random.uniform(0.3, 0.6))

    pass_input = page.locator('input[type="password"]').first
    await pass_input.click()
    await pass_input.fill("")
    await human_type(pass_input, password)
    await asyncio.sleep(random.uniform(0.3, 0.6))

    print("[*] Clicking login button ...")
    clicked = False
    for label in ["Login", "Log In", "Sign In", "Submit"]:
        btn = page.locator(f'button:has-text("{label}")').last
        if await btn.count():
            await btn.click()
            clicked = True
            print(f"[*] Clicked '{label}' button.")
            break
    if not clicked:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] Could not find login button. Screenshot -> tingg_error.png")

    print("[*] Waiting for post-login redirect ...")
    try:
        await page.wait_for_url(lambda url: "login" not in url, timeout=30_000)
    except PlaywrightTimeoutError:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] Login failed — still on login page. Check credentials. Screenshot -> tingg_error.png")

    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    await asyncio.sleep(2.0)
    print(f"[+] Login successful. URL: {page.url}")


# ---------------------------------------------------------------------------
# NAVIGATE TO PAYMENTS
# ---------------------------------------------------------------------------

async def navigate_to_payments(page):
    print("\n[*] Navigating to Transactions > Payments ...")
    try:
        await page.goto(PAYMENTS_URL, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        pass

    if "login" in page.url:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] Redirected to login — session not established. Screenshot -> tingg_error.png")

    try:
        await page.wait_for_selector('button:has-text("All filters")', timeout=20_000)
    except PlaywrightTimeoutError:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] Payments page did not load properly. Screenshot -> tingg_error.png")

    print(f"[*] Payments page loaded. URL: {page.url}")


# ---------------------------------------------------------------------------
# DATE FILTER  (OWL DateTime Picker — input id="fromDate")
# ---------------------------------------------------------------------------

async def set_start_date(page, start: date):
    await page.evaluate("window.scrollBy(0, 650)")
    await asyncio.sleep(0.5)

    print("[*] Ensuring All Filters panel is open ...")
    if not await page.locator('#fromDate').is_visible():
        await page.locator('button:has-text("All filters")').first.click()
        await asyncio.sleep(0.8)

    print(f"[*] Opening start date calendar (target: {start}) ...")
    await page.locator('#fromDate').click()
    await asyncio.sleep(1.5)

    try:
        await page.wait_for_selector('.owl-dt-calendar-cell', timeout=8_000)
    except PlaywrightTimeoutError:
        await page.screenshot(path="tingg_error.png")
        sys.exit("[!] OWL datepicker calendar did not open. Screenshot -> tingg_error.png")

    target_str = str(start.day)
    clicked = await page.evaluate(f"""
        () => {{
            const cells = document.querySelectorAll('td.owl-dt-calendar-cell');
            for (const td of cells) {{
                const span = td.querySelector('span.owl-dt-calendar-cell-content');
                if (!span) continue;
                if (span.classList.contains('owl-dt-calendar-cell-out')) continue;
                if (span.textContent.trim() === '{target_str}') {{
                    td.click();
                    return true;
                }}
            }}
            return false;
        }}
    """)

    if not clicked:
        await page.screenshot(path="tingg_error.png")
        sys.exit(f"[!] Could not find day {start.day} in OWL calendar. Screenshot -> tingg_error.png")

    await asyncio.sleep(0.5)
    print(f"[*] Day {start.day} clicked.")

    await page.locator('button.owl-dt-container-control-button:has-text("Set")').click()
    await asyncio.sleep(0.5)
    print(f"[*] Start date confirmed: {start}")


# ---------------------------------------------------------------------------
# COUNTRY SELECTION  (ng-select component, class="ng-select-country")
# ---------------------------------------------------------------------------

async def select_country(page, country: str):
    print(f"[*] Selecting country: {country} ...")
    await page.locator('ng-select.ng-select-country .ng-select-container').click()
    await asyncio.sleep(0.8)

    try:
        await page.wait_for_selector('ng-dropdown-panel .ng-option', timeout=8_000)
    except PlaywrightTimeoutError:
        await page.screenshot(path="tingg_error.png")
        sys.exit(f"[!] Country dropdown did not open. Screenshot -> tingg_error.png")

    clicked = await page.evaluate(f"""
        () => {{
            const options = document.querySelectorAll('ng-dropdown-panel .ng-option');
            for (const opt of options) {{
                if (opt.textContent.trim() === '{country}') {{
                    opt.click();
                    return true;
                }}
            }}
            return false;
        }}
    """)

    if not clicked:
        await page.locator(f'ng-dropdown-panel .ng-option:has-text("{country}")').first.click()

    await asyncio.sleep(1.5)
    print(f"[*] Country set to {country}.")


# ---------------------------------------------------------------------------
# S3 UPLOAD + DOWNLOAD CSV
# ---------------------------------------------------------------------------

def _upload_to_s3(temp_path: str, filename: str) -> None:
    aws_key    = _require_env("AWS_ACCESS_KEY_ID")
    aws_secret = _require_env("AWS_SECRET_ACCESS_KEY")
    s3_key     = S3_PREFIX + filename
    print(f"[*] Uploading to s3://{S3_BUCKET}/{s3_key} ...")
    s3 = boto3.client(
        "s3",
        aws_access_key_id=aws_key,
        aws_secret_access_key=aws_secret,
        region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"),
    )
    s3.upload_file(temp_path, S3_BUCKET, s3_key)
    print(f"[+] Upload complete -> s3://{S3_BUCKET}/{s3_key}")


async def download_csv(page, country: str, start: date):
    print(f"[*] Downloading CSV for {country} ...")

    dl_btn = page.locator('button:has-text("Download")')
    await dl_btn.scroll_into_view_if_needed()
    await dl_btn.click()
    await asyncio.sleep(0.5)

    await page.locator('text="CSV"').click()
    await asyncio.sleep(0.6)

    if await page.locator('text="I agree"').count():
        await page.locator('label:has-text("I agree")').click()
        await asyncio.sleep(0.3)
        async with page.expect_download(timeout=60_000) as dl_info:
            await page.locator('button:has-text("Yes, Export")').click()
    else:
        async with page.expect_download(timeout=60_000) as dl_info:
            await page.locator('button:has-text("Yes, Export")').click()

    download  = await dl_info.value
    today     = date.today()
    filename  = f"tingg_payments_{country.lower()}_{start}_{today}.csv"
    temp_path = await download.path()
    print(f"[*] Download captured: {temp_path}")
    _upload_to_s3(temp_path, filename)
    await download.delete()
    print("[*] Temp file deleted.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

async def main():
    today      = date.today()
    start_date = today - timedelta(days=10)

    async with async_playwright() as pw:
        headless = os.environ.get("TINGG_HEADLESS", "false").lower() == "true"
        browser  = await pw.chromium.launch(
            headless=headless,
            slow_mo=80,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="Africa/Nairobi",
            accept_downloads=True,
        )
        page = await context.new_page()

        await login(page)
        await navigate_to_payments(page)

        for country in COUNTRIES:
            print(f"\n--- {country} ---")
            await select_country(page, country)
            await set_start_date(page, start_date)
            await download_csv(page, country, start_date)

        await browser.close()
        print(f"\n[+] All done. Files uploaded to s3://{S3_BUCKET}/{S3_PREFIX}")


if __name__ == "__main__":
    asyncio.run(main())
