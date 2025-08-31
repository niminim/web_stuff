import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Setup browser (keeps it open for efficiency)
options = Options()
options.add_argument("--headless")  # Run in headless mode to save resources
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# URL of the event page
url = "https://tickets.sf-f.org.il/olamot2025/"

try:
    while True:  # Infinite loop to check every 10 seconds
        driver.get(url)  # Reload page instead of restarting browser
        wait = WebDriverWait(driver, 15)

        # Scroll to ensure JavaScript-rendered content loads
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)  # Let JavaScript finish loading

        # Locate the <td> container that contains the event
        event_td = wait.until(EC.presence_of_element_located(
            (By.XPATH, "//td[@id='109' and contains(@class, 'session')]")))

        # Use JavaScript to extract text (works for hidden or nested elements)
        category = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[contains(@class, 'category')]")).strip()
        title = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[@class='event_title']")).strip()
        speaker = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[@class='speakers']")).strip()
        age_restriction = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[contains(@class, 'age_restriction')]")).strip()
        tickets_left = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[contains(@class, 'cod_tickets_remain')]")).strip()
        sale_status = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//span[contains(@class, 'wpfp-span')]")).strip()

        # Extract additional details from sessionmeta
        location = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[span[contains(text(), 'מיקום')]]/span[@class='sessionmetavalue']")).strip()
        price = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[span[contains(text(), 'מחיר')]]/span[@class='sessionmetavalue']")).strip()
        description = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[contains(@class, 'sessiondescription')]")).strip()

        # Clean up HTML artifacts and fix price formatting
        price = re.sub(r"<b>|</b>", "", price).replace("<br>", "\n").replace("תעריף", "\nתעריף")  # Fixes spacing

        # Print extracted info
        print(f"🔍 Checked at {time.strftime('%H:%M:%S')} - Tickets Left: {tickets_left}")
        print(f"📌 Category: {category}")
        print(f"📌 Title: {title}")
        print(f"📌 Speaker: {speaker}")
        print(f"📌 Age Restriction: {age_restriction}")
        print(f"📌 Sale Status: {sale_status}")
        print(f"📌 Location: {location}")
        print(f"📌 Price:\n{price}")

        time.sleep(10)  # Wait 10 seconds before checking again

except KeyboardInterrupt:
    print("\n🛑 Monitoring stopped by user.")

finally:
    driver.quit()  # Close browser when script stops
