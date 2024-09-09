import requests

# URL of the webpage you want to fetch
url = 'https://www.timeanddate.com/weather/israel/tel-aviv'

# Send an HTTP GET request to the URL
response = requests.get(url)

# Check if the request was successful
if response.status_code == 200:
    # Get the entire HTML content of the page
    html_content = response.text

    # Print the HTML content
    print(html_content)
else:
    print(f"Failed to retrieve data. Status code: {response.status_code}")

#############
import requests
from bs4 import BeautifulSoup

# URL of the weather page
url = 'https://www.timeanddate.com/weather/israel/tel-aviv'

# Send a GET request to the URL
response = requests.get(url)

# Check if the request was successful
if response.status_code == 200:
    # Parse the HTML content using BeautifulSoup
    soup = BeautifulSoup(response.text, 'html.parser')

    # Find the element that contains the text "Wind: " and get the following text
    wind_section = soup.find(string=lambda x: "Wind:" in x)

    # If the wind section is found, extract the relevant text
    if wind_section:
        # Get the text after "Wind:" and strip any extra whitespace
        wind_info = wind_section.strip()
        print(wind_info)
    else:
        print("Wind information not found.")
else:
    print(f"Failed to retrieve data. Status code: {response.status_code}")