# import requests
# import re
# import time
#
# # URL of the Headstart campaign
# url = 'https://headstart.co.il/project/81508'
#
#
# # Function to extract the pledged amount and number of pledgers
# def get_campaign_data():
#     response = requests.get(url)
#     if response.status_code != 200:
#         print("Error fetching the webpage.")
#         return None, None
#
#     # Use regex to extract the totalFunded and totalPledgers values
#     amount_match = re.search(r'"totalFunded":"(\d+\.\d+)"', response.text)
#     pledgers_match = re.search(r'"totalPledgers":(\d+)', response.text)
#
#     if amount_match and pledgers_match:
#         amount = int(float(amount_match.group(1)))  # Convert to integer
#         pledgers = int(pledgers_match.group(1))  # Convert to integer
#         return amount, pledgers
#     else:
#         print("Could not find the campaign data.")
#         return None, None
#
#
# # Initialize the last known values
# last_amount, last_pledgers = get_campaign_data()
# if last_amount is None or last_pledgers is None:
#     print("Exiting script due to error in fetching initial data.")
#     exit()
#
# print(f"Initial pledged amount: {last_amount}₪ | Total pledgers: {last_pledgers}")
#
# # Monitor the campaign for changes
# try:
#     while True:
#         current_amount, current_pledgers = get_campaign_data()
#         if current_amount is None or current_pledgers is None:
#             print("Error fetching campaign data. Retrying...")
#         else:
#             if current_amount > last_amount:
#                 print(f"🚀 New pledge detected! Total pledged amount is now {current_amount}₪")
#                 last_amount = current_amount  # Update the last known amount
#
#             if current_pledgers > last_pledgers:
#                 print(f"👥 New pledger detected! Total pledgers: {current_pledgers}")
#                 last_pledgers = current_pledgers  # Update the last known pledgers
#
#         # Wait for a specified interval before checking again (e.g., 5 minutes)
#         time.sleep(30)
# except KeyboardInterrupt:
#     print("Script terminated by user.")



import requests
import re
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Email Configuration
SMTP_SERVER = "smtp.gmail.com"  # Use your SMTP server (e.g., "smtp.office365.com" for Outlook)
SMTP_PORT = 587
EMAIL_SENDER = "nimroddaniel1986@gmail.com"  # Your email
EMAIL_PASSWORD = "oefjlamuyuiqeqln"  # Your Gmail App Password
EMAIL_RECIPIENTS = ["nimroddaniel1986@gmail.com"]  # Email where notifications will be sent
EMAIL_RECIPIENTS2 = ["nimroddaniel1986@gmail.com", "ronilondon2@gmail.com",]  # Email where notifications will be sent

# URL of the Headstart campaign
url = 'https://headstart.co.il/project/81508'

# Flags to track milestone emails
milestone_reached_100 = True
milestone_reached_120 = False


# Function to send email notification to multiple recipients
def send_email(subject, body, email_recipients):
    try:
        if not email_recipients:
            print("⚠️ No email recipients provided. Email not sent.")
            return

        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = ", ".join(email_recipients)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, email_recipients, msg.as_string())

        print(f"📧 Email sent to: {', '.join(email_recipients)} | Subject: {subject}")

    except Exception as e:
        print(f"⚠️ Error sending email: {e}")


# Function to extract campaign data
def get_campaign_data():
    response = requests.get(url)
    if response.status_code != 200:
        print("⚠️ Error fetching the webpage.")
        return None, None, None

    target_match = re.search(r'"target":"(\d+\.\d+)"', response.text)
    amount_match = re.search(r'"totalFunded":"(\d+\.\d+)"', response.text)
    pledgers_match = re.search(r'"totalPledgers":(\d+)', response.text)

    if target_match and amount_match and pledgers_match:
        target = int(float(target_match.group(1)))
        amount = int(float(amount_match.group(1)))
        pledgers = int(pledgers_match.group(1))
        return target, amount, pledgers
    else:
        print("⚠️ Could not find campaign data.")
        return None, None, None


# Initialize campaign data
target, last_amount, last_pledgers = get_campaign_data()
if target is None or last_amount is None or last_pledgers is None:
    print("❌ Exiting script due to error in fetching initial data.")
    exit()

# Initial progress
progress = round((last_amount / target) * 100, 2)

# Send initial monitoring email
subject = "📊 Campaign Monitoring Started"
body = f"The script has started monitoring the campaign!\n\n" \
       f"🎯 Target Goal: {target}₪\n" \
       f"💰 Initial Pledged Amount: {last_amount}₪\n" \
       f"📈 Progress: {progress}%\n" \
       f"👥 Total Pledgers: {last_pledgers}\n\n" \
       f"🔗 Check the campaign here: {url}"
send_email(subject, body, EMAIL_RECIPIENTS)

print(f"✅ Monitoring started | Target: {target}₪ | Initial Amount: {last_amount}₪ | Progress: {progress}% | Pledgers: {last_pledgers}")

# Monitoring loop
try:
    while True:
        _, current_amount, current_pledgers = get_campaign_data()
        if current_amount is None or current_pledgers is None:
            print("⚠️ Error fetching campaign data. Retrying...")
        else:
            progress = round((current_amount / target) * 100, 2)
            amount_increase = current_amount - last_amount

            # New pledge detected
            if current_amount > last_amount:
                subject = "🚀 New Pledge Alert!"
                body = f"A new pledge was made!\n\n" \
                       f"🎯 Target: {target}₪\n" \
                       f"💰 Total pledged amount: {current_amount}₪ (+{amount_increase}₪)\n" \
                       f"📈 Progress: {progress}%\n" \
                       f"👥 Total Pledgers: {current_pledgers}\n\n" \
                       f"🔗 Check the campaign here: {url}"
                send_email(subject, body, EMAIL_RECIPIENTS)
                last_amount = current_amount
                last_pledgers = current_pledgers

            # Milestone 100% reached
            if progress >= 100 and not milestone_reached_100:
                milestone_reached_100 = True
                subject = "🎉 רוני, עשית את זה!!!! 🎉"
                body = f"איילון, רוני וחברים יוצאים לאור 🎉\n\n" \
                       f"🎯 Target: {target}₪\n" \
                       f"💰 Total pledged amount: {current_amount}₪ (+{amount_increase}₪)\n" \
                       f"📈 Progress: {progress}%\n" \
                       f"👥 Total Pledgers: {current_pledgers}\n\n" \
                       f"Keep going! 🚀\n\n" \
                       f"🔗 Check the campaign here: {url}"
                send_email(subject, body, EMAIL_RECIPIENTS2)

            # Milestone 120% reached
            if progress >= 120 and not milestone_reached_120:
                milestone_reached_120 = True
                subject = "🎉 רוני, ברכותיי, הפסדת בגדול! 🎉"
                body = f"עכשיו את צריכה לצלם את עצמך (מהצד או מאחורה) כשאת אומרת - הפסדתי בהתערבות מול כל כך הרבה אנשים שאני ממש מתביישת\n\n" \
                       f"🎯 Target: {target}₪\n" \
                       f"💰 Total pledged amount: {current_amount}₪ (+{amount_increase}₪)\n" \
                       f"📈 Progress: {progress}%\n" \
                       f"👥 Total Pledgers: {current_pledgers}\n\n" \
                       f"Keep going! 🚀\n\n" \
                       f"🔗 Check the campaign here: {url}"
                send_email(subject, body, EMAIL_RECIPIENTS2)

        # Wait before checking again
        time.sleep(10)

except KeyboardInterrupt:
    print("❌ Script terminated by user.")
