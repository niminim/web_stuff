import time
import smtplib
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Email settings (CHANGE THESE)
GMAIL_USER = "nimroddaniel1986@gmail.com"  # Replace with your email
GMAIL_PASS = "oefjlamuyuiqeqln"  # Use an app password for security
TO_EMAILS = ["nimroddaniel1986@gmail.com", "nimroddaniel1986@gmail.com"]  # List of recipients

# Function to send email notification
def send_email(subject, body):
    msg = MIMEText(body)
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(TO_EMAILS)
    msg["Subject"] = subject

    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)  # Connect to Gmail
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, TO_EMAILS, msg.as_string())
        server.quit()
        print("📧 Email notification sent!")
    except Exception as e:
        print("❌ Failed to send email:", e)

# Setup browser (keeps it open for efficiency)
options = Options()
options.add_argument("--headless")  # Run in headless mode to save resources
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# URL of the event page
url = "https://tickets.sf-f.org.il/olamot2025/"

# Variables to track previous values
previous_sale_status = None
previous_tickets_left = None
first_check_done = False

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

        # Extract tickets left and sale status
        tickets_left = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//div[contains(@class, 'cod_tickets_remain')]")).strip()
        sale_status = driver.execute_script("return arguments[0].innerText;", event_td.find_element(By.XPATH, ".//span[contains(@class, 'wpfp-span')]")).strip()

        # Print extracted info
        print(f"🔍 Checked at {time.strftime('%H:%M:%S')} - Tickets Left: {tickets_left}, Sale Status: {sale_status}")

        # Send email on first check
        if not first_check_done:
            email_subject = "🎟️ Initial Event Status Update"
            email_body = f"First check completed.\n\nChecked at: {time.strftime('%Y-%m-%d %H:%M:%S')}\nTickets Left: {tickets_left}\nSale Status: {sale_status}\n\nCheck the event page: {url}"
            send_email(email_subject, email_body)
            first_check_done = True  # Prevent further first-check emails

        # Check if sale status or tickets count changed and send an email
        if (previous_sale_status is not None and sale_status != previous_sale_status) or \
           (previous_tickets_left is not None and tickets_left != previous_tickets_left):

            email_subject = "⚠️ Event Update: Change Detected!"
            email_body = f"An update has been detected in the event.\n\nChecked at: {time.strftime('%Y-%m-%d %H:%M:%S')}\nPrevious Sale Status: {previous_sale_status}\nNew Sale Status: {sale_status}\nPrevious Tickets Left: {previous_tickets_left}\nNew Tickets Left: {tickets_left}\n\nCheck the event page: {url}"
            send_email(email_subject, email_body)

        # Update previous values
        previous_sale_status = sale_status
        previous_tickets_left = tickets_left

        time.sleep(10)  # Wait 10 seconds before checking again

except KeyboardInterrupt:
    print("\n🛑 Monitoring stopped by user.")

finally:
    driver.quit()  # Close browser when script stops
